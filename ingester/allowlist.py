"""The columns that may land, per table, for each schema version (ADR 0005 item 4, prv_01).

This is the single list of what a snapshot's Parquet files and the `raw` tables
may hold. A field is added here, with a schema_version bump, or it does not
land. One delivery table holds every stored delivery with nullable columns per
packet type, because the staging views (warehouse-eng) select by portnum and
position_source from one row per delivery; the two count tables hold what is
counted instead of stored. Position packets and MapReports share the position
columns: MapReport `latitude_i`, `longitude_i`, `altitude` and
`position_precision` land in `latitude_i`, `longitude_i`, `altitude_m` and
`precision_bits`, with `position_source` saying which message they came from.
NeighborInfo neighbours land as four arrays of equal length, in message order.

Column names keep the protobuf field names on purpose (`hw_model`,
`latitude_i`, `air_util_tx`, `channel_utilization`), so a reader can look a
column up in the Meshtastic protobuf definitions. Units are the protobuf's:
`latitude_i` and `longitude_i` in 1e-7 degrees (EPSG:4326), `altitude_m` in
metres, `rx_snr` in dB, `rx_rssi` in dBm, `rx_time`, `position_time` and
`neighbour_last_heard` in Unix seconds, `uptime_s` in seconds,
`channel_utilization` and `air_util_tx` in percent, `battery_level` in percent
(101 means powered).

Not set lands as null. Where the protobuf field has presence (Position
`latitude_i`, `longitude_i`, `altitude`, MapReport fields read as optional) the
decoder asks the message; the MeshPacket header fields `packet_id`,
`relay_node`, `hop_start`, `rx_time`, `rx_snr`, `rx_rssi` and `channel` have
no presence, and 0 there means unset, so 0 lands as null. `hop_limit` stays
not null: 0 is a real value, the public broker's rewrite of every default-key
packet (docs/PLAN.md).
"""

from dataclasses import dataclass

import pyarrow as pa  # type: ignore[import-untyped]

SCHEMA_VERSION = 1
DECODER_VERSION = "1"


@dataclass(frozen=True)
class Column:
    """One landed column with its PostgreSQL type."""

    name: str
    sql_type: str
    nullable: bool = False


def _versions() -> tuple[Column, ...]:
    return (Column("decoder_version", "text"), Column("schema_version", "integer"))


MQTT_MESSAGE = (
    Column("snapshot_id", "text"),
    Column("delivery_key", "text"),
    Column("received_at", "timestamp with time zone"),
    Column("topic", "text"),
    Column("modem_preset", "text", nullable=True),
    Column("seen_public", "boolean"),
    Column("seen_meshstl", "boolean"),
    Column("decode_status", "text"),
    Column("gateway_pseudonym", "text", nullable=True),
    Column("gateway_low_byte", "smallint", nullable=True),
    Column("origin_pseudonym", "text"),
    Column("origin_low_byte", "smallint"),
    Column("packet_id", "bigint", nullable=True),
    Column("relay_node", "smallint", nullable=True),
    Column("hop_start", "smallint", nullable=True),
    Column("hop_limit", "smallint"),
    Column("via_mqtt", "boolean"),
    Column("rx_time", "bigint", nullable=True),
    Column("rx_snr", "real", nullable=True),
    Column("rx_rssi", "integer", nullable=True),
    Column("channel", "bigint", nullable=True),
    Column("portnum", "integer", nullable=True),
    Column("payload_length", "integer"),
    Column("position_source", "text", nullable=True),
    Column("latitude_i", "integer", nullable=True),
    Column("longitude_i", "integer", nullable=True),
    Column("altitude_m", "integer", nullable=True),
    Column("precision_bits", "smallint", nullable=True),
    Column("position_time", "bigint", nullable=True),
    Column("location_source", "text", nullable=True),
    Column("altitude_source", "text", nullable=True),
    Column("hw_model", "text", nullable=True),
    Column("role", "text", nullable=True),
    Column("neighbour_pseudonyms", "text[]", nullable=True),
    Column("neighbour_low_bytes", "smallint[]", nullable=True),
    Column("neighbour_snrs", "real[]", nullable=True),
    Column("neighbour_last_heard", "bigint[]", nullable=True),
    Column("channel_utilization", "real", nullable=True),
    Column("air_util_tx", "real", nullable=True),
    Column("uptime_s", "bigint", nullable=True),
    Column("battery_level", "integer", nullable=True),
    *_versions(),
)

MQTT_COPY_COUNT = (
    Column("snapshot_id", "text"),
    Column("hour", "timestamp with time zone"),
    Column("topic", "text"),
    Column("gateway_pseudonym", "text"),
    Column("gateway_low_byte", "smallint"),
    Column("copy_count", "bigint"),
    *_versions(),
)

MQTT_DROPPED_COUNT = (
    Column("snapshot_id", "text"),
    Column("hour", "timestamp with time zone"),
    Column("topic_class", "text"),
    Column("reason", "text"),
    Column("delivery_count", "bigint"),
    *_versions(),
)

TABLES: dict[str, tuple[Column, ...]] = {
    "mqtt_message": MQTT_MESSAGE,
    "mqtt_copy_count": MQTT_COPY_COUNT,
    "mqtt_dropped_count": MQTT_DROPPED_COUNT,
}

# Unique keys of the raw tables (ing_01, ing_03, ing_07, snp_01).
KEYS: dict[str, tuple[str, ...]] = {
    "mqtt_message": ("snapshot_id", "delivery_key"),
    "mqtt_copy_count": ("snapshot_id", "hour", "topic", "gateway_pseudonym"),
    "mqtt_dropped_count": ("snapshot_id", "hour", "topic_class", "reason"),
    "snapshot": ("snapshot_id",),
}

DECODE_STATUSES = ("ok", "unknown_port", "decrypt_failed", "stripped", "malformed")
POSITION_SOURCES = ("position", "mapreport")


def allowlist(table: str) -> list[str]:
    """Column names of a landed table, in file order.

    Raises:
        KeyError: if the table is not one the reduction or a capture writes.
    """
    return [column.name for column in TABLES[table]]


_ARROW_TYPES: dict[str, pa.DataType] = {
    "text": pa.string(),
    "timestamp with time zone": pa.timestamp("us", tz="UTC"),
    "boolean": pa.bool_(),
    "smallint": pa.int16(),
    "integer": pa.int32(),
    "bigint": pa.int64(),
    "real": pa.float32(),
    "text[]": pa.list_(pa.string()),
    "smallint[]": pa.list_(pa.int16()),
    "real[]": pa.list_(pa.float32()),
    "bigint[]": pa.list_(pa.int64()),
}


def arrow_schema(table: str) -> pa.Schema:
    """The Parquet schema of a landed table, derived from its allowlist.

    Raises:
        KeyError: if the table is not one the reduction or a capture writes.
    """
    return pa.schema(
        [
            pa.field(column.name, _ARROW_TYPES[column.sql_type], nullable=column.nullable)
            for column in TABLES[table]
        ]
    )
