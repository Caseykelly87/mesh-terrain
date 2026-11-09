"""The landed columns are the ADR 0005 allowlist, and the raw DDL matches it (prv_01, ing_01).

The Parquet half of the prv_01 schema test (each landed table's schema equals
the allowlist for its schema_version) needs the Parquet writer and lands with
the decoder.
"""

import re
from pathlib import Path

import pytest

from ingester.allowlist import (
    DECODER_VERSION,
    KEYS,
    SCHEMA_VERSION,
    TABLES,
    allowlist,
)

DDL_PATH = Path(__file__).resolve().parents[1] / "ingester" / "sql" / "raw_tables.sql"

# Every field ADR 0005 item 4 allows, with the column that carries it.
ADR_FIELDS = {
    "received_at": "received_at",
    "gateway pseudonym": "gateway_pseudonym",
    "gateway low byte": "gateway_low_byte",
    "origin pseudonym": "origin_pseudonym",
    "origin low byte": "origin_low_byte",
    "packet_id": "packet_id",
    "relay_node byte": "relay_node",
    "hop_start": "hop_start",
    "hop_limit": "hop_limit",
    "via_mqtt": "via_mqtt",
    "rx_time": "rx_time",
    "rx_snr": "rx_snr",
    "rx_rssi": "rx_rssi",
    "channel": "channel",
    "topic": "topic",
    "modem preset": "modem_preset",
    "portnum": "portnum",
    "payload length": "payload_length",
    "seen_public": "seen_public",
    "seen_meshstl": "seen_meshstl",
    "position lat": "latitude_i",
    "position lon": "longitude_i",
    "position altitude": "altitude_m",
    "position precision_bits": "precision_bits",
    "position timestamp": "position_time",
    "position location_source": "location_source",
    "position altitude_source": "altitude_source",
    "position_source": "position_source",
    "mapreport latitude": "latitude_i",
    "mapreport longitude": "longitude_i",
    "mapreport position_precision": "precision_bits",
    "mapreport altitude": "altitude_m",
    "nodeinfo hardware model": "hw_model",
    "nodeinfo role": "role",
    "neighbour pseudonym": "neighbour_pseudonyms",
    "neighbour low byte": "neighbour_low_bytes",
    "neighbour snr": "neighbour_snrs",
    "neighbour last heard": "neighbour_last_heard",
    "telemetry channel_utilization": "channel_utilization",
    "telemetry air_util_tx": "air_util_tx",
    "telemetry uptime": "uptime_s",
    "telemetry battery level": "battery_level",
}
# Columns that are not message fields: identity, provenance and decode outcome.
BOOKKEEPING = {"snapshot_id", "delivery_key", "decode_status", "decoder_version", "schema_version"}
FORBIDDEN = re.compile(r"payload$|^payload_bytes|text|long_name|short_name|public_key|node_id|mac")


def parse_ddl(sql: str) -> dict[str, list[tuple[str, str, bool]]]:
    """Columns per raw table as (name, type, not null), from the DDL file."""
    tables: dict[str, list[tuple[str, str, bool]]] = {}
    for match in re.finditer(r"create table if not exists raw\.(\w+) \((.*?)\n\);", sql, re.S):
        columns = []
        for line in match.group(2).splitlines():
            line = line.strip().rstrip(",")
            column = re.match(
                r"(\w+) ([a-z0-9 \[\]]+?)( not null)?(?: references .*| check .*)?$", line
            )
            if line and not line.startswith(("primary key", "--")) and column:
                columns.append((column.group(1), column.group(2), column.group(3) is not None))
        tables[match.group(1)] = columns
    return tables


def primary_keys(sql: str) -> dict[str, tuple[str, ...]]:
    keys = {}
    for match in re.finditer(r"create table if not exists raw\.(\w+) \((.*?)\n\);", sql, re.S):
        key = re.search(r"primary key \(([^)]*)\)", match.group(2))
        assert key is not None, match.group(1)
        keys[match.group(1)] = tuple(part.strip() for part in key.group(1).split(","))
    return keys


def test_every_adr_field_has_a_column_and_nothing_else_lands() -> None:
    columns = set(allowlist("mqtt_message"))
    assert set(ADR_FIELDS.values()) <= columns
    assert columns - set(ADR_FIELDS.values()) == BOOKKEEPING


@pytest.mark.parametrize("table", sorted(TABLES))
def test_no_column_can_hold_bytes_text_names_or_keys(table: str) -> None:
    for name in allowlist(table):
        assert not FORBIDDEN.search(name), name


def test_versions_are_set() -> None:
    assert SCHEMA_VERSION == 1
    assert DECODER_VERSION


@pytest.mark.parametrize("table", sorted(TABLES))
def test_every_table_carries_both_versions_and_the_snapshot(table: str) -> None:
    assert {"snapshot_id", "decoder_version", "schema_version"} <= set(allowlist(table))


def test_ddl_columns_equal_the_allowlist() -> None:
    ddl = parse_ddl(DDL_PATH.read_text(encoding="utf-8"))
    for table, columns in TABLES.items():
        expected = [(column.name, column.sql_type, not column.nullable) for column in columns]
        assert ddl[table] == expected, table


def test_ddl_keys_are_the_contract_keys() -> None:
    keys = primary_keys(DDL_PATH.read_text(encoding="utf-8"))
    assert keys["mqtt_message"] == ("snapshot_id", "delivery_key")
    assert keys["mqtt_copy_count"] == ("snapshot_id", "hour", "topic", "gateway_pseudonym")
    assert keys["mqtt_dropped_count"] == ("snapshot_id", "hour", "topic_class", "reason")
    assert keys["snapshot"] == ("snapshot_id",)
    for table, key in KEYS.items():
        assert keys[table] == key


def test_ddl_has_no_update_or_delete() -> None:
    # ing_07: rows in raw are never updated or deleted one by one.
    sql = DDL_PATH.read_text(encoding="utf-8").lower()
    assert not re.search(r"\b(update|delete|truncate|drop)\b", sql)
