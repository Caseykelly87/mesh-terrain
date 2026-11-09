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
"""

from dataclasses import dataclass

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
    Column("packet_id", "bigint"),
    Column("relay_node", "smallint"),
    Column("hop_start", "smallint"),
    Column("hop_limit", "smallint"),
    Column("via_mqtt", "boolean"),
    Column("rx_time", "bigint"),
    Column("rx_snr", "real"),
    Column("rx_rssi", "integer"),
    Column("channel", "bigint"),
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
