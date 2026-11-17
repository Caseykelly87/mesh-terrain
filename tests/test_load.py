"""Loading a snapshot into raw (ing_01, ing_03, ing_04, ing_05, ing_07, snp_01).

The database tests load into a throwaway schema built from the raw DDL
(tests/raw_schema.py), inside a transaction that is rolled back, and never
touch `raw` or `raw_dev_wh` (Casey, issue #55, item 10). They need the local
stack (`make up`); where no warehouse answers, as in CI's tests job, they are
skipped and say so.
"""

import inspect
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog
from psycopg import sql
from raw_schema import ThrowawayRaw, throwaway_raw_schema
from test_reduce import INFO, KEY, SNAPSHOT_ID, table_rows, write_archive

from ingester import load
from ingester.reduce import reduce_capture
from pipeline.common.snapshot import SnapshotError, read_manifest

LOADER_SOURCE = Path(load.__file__).read_text(encoding="utf-8")
SCHEMA_TAKERS = (
    "apply_ddl",
    "check_columns",
    "load_table",
    "load_snapshot",
    "row_counts",
    "reconcile",
    "decode_rates",
    "zero_positions",
    "content_hashes",
    "hash_problems",
)


@pytest.fixture
def raw() -> Iterator[ThrowawayRaw]:
    with throwaway_raw_schema() as throwaway:
        yield throwaway


@pytest.fixture
def snapshot(tmp_path: Path) -> Path:
    sources = write_archive(tmp_path / "archive")
    return reduce_capture(sources, tmp_path / "snapshots", INFO, KEY, workers=1)


def message_table(raw: ThrowawayRaw) -> sql.Identifier:
    return sql.Identifier(raw.schema, "mqtt_message")


def test_the_loader_never_updates_or_deletes() -> None:
    # ing_07: rows in raw are only ever inserted.
    assert not re.search(r"\b(update|delete|truncate|drop)\b", LOADER_SOURCE, re.IGNORECASE)


@pytest.mark.parametrize("name", SCHEMA_TAKERS)
def test_every_loader_query_takes_a_schema_that_defaults_to_raw(name: str) -> None:
    # Production calls without the argument keep loading into raw.
    parameter = inspect.signature(getattr(load, name)).parameters["schema"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default == "raw"


def test_the_ddl_names_only_the_schema_it_is_given() -> None:
    default = load.raw_ddl().as_string(None)
    other = load.raw_ddl("test_load_000000000000").as_string(None)
    assert default.count('create table if not exists "raw".') == 4
    assert other.count('create table if not exists "test_load_000000000000".') == 4
    assert not re.search(r"\braw\.", other)


def test_load_inserts_every_row_once(raw: ThrowawayRaw, snapshot: Path) -> None:
    inserted = load.load_snapshot(raw.conn, snapshot, schema=raw.schema)
    for table in ("mqtt_message", "mqtt_copy_count", "mqtt_dropped_count"):
        assert inserted[table] == len(table_rows(snapshot, table)), table
    assert inserted["snapshot"] == 1
    counts = load.row_counts(raw.conn, SNAPSHOT_ID, schema=raw.schema)
    assert counts["mqtt_message"] == inserted["mqtt_message"]


def test_the_load_logs_its_schema(raw: ThrowawayRaw, snapshot: Path) -> None:
    with structlog.testing.capture_logs() as logs:
        load.load_snapshot(raw.conn, snapshot, schema=raw.schema)
    events = {entry["event"]: entry for entry in logs}
    assert events["load_snapshot"]["schema"] == raw.schema
    assert {entry["schema"] for entry in logs if entry["event"] == "load_table"} == {raw.schema}


def test_a_second_load_changes_nothing(raw: ThrowawayRaw, snapshot: Path) -> None:
    load.load_snapshot(raw.conn, snapshot, schema=raw.schema)
    before = load.row_counts(raw.conn, SNAPSHOT_ID, schema=raw.schema)
    again = load.load_snapshot(raw.conn, snapshot, schema=raw.schema)
    assert set(again.values()) == {0}
    assert load.row_counts(raw.conn, SNAPSHOT_ID, schema=raw.schema) == before


def test_a_changed_file_stops_the_load_before_any_insert(raw: ThrowawayRaw, snapshot: Path) -> None:
    path = sorted((snapshot / "mqtt_copy_count").glob("*.parquet"))[0]
    path.write_bytes(path.read_bytes() + b"\x00")
    with pytest.raises(SnapshotError):
        load.load_snapshot(raw.conn, snapshot, schema=raw.schema)
    assert set(load.row_counts(raw.conn, SNAPSHOT_ID, schema=raw.schema).values()) == {0}


def test_schema_drift_is_refused(raw: ThrowawayRaw, snapshot: Path) -> None:
    raw.conn.execute(
        sql.SQL("alter table {} add column drift_test integer").format(message_table(raw))
    )
    with pytest.raises(load.SchemaDriftError, match=f"{raw.schema}.mqtt_message"):
        load.load_snapshot(raw.conn, snapshot, schema=raw.schema)


def test_reconciliation_passes_on_a_loaded_snapshot(raw: ThrowawayRaw, snapshot: Path) -> None:
    load.load_snapshot(raw.conn, snapshot, schema=raw.schema)
    assert load.reconcile(raw.conn, snapshot, schema=raw.schema) == []


def test_an_extra_count_row_is_a_reconcile_problem(raw: ThrowawayRaw, snapshot: Path) -> None:
    load.load_snapshot(raw.conn, snapshot, schema=raw.schema)
    raw.conn.execute(
        sql.SQL(
            "insert into {} values (%s, '2026-01-01T07:00:00+00:00', 'other', 'json', 1, '1', 1)"
        ).format(sql.Identifier(raw.schema, "mqtt_dropped_count")),
        (SNAPSHOT_ID,),
    )
    assert load.reconcile(raw.conn, snapshot, schema=raw.schema) == ["counted: 2026-01-01T07"]


def test_a_wrong_broker_flag_is_a_reconcile_problem_for_that_broker(
    raw: ThrowawayRaw, snapshot: Path
) -> None:
    load.load_snapshot(raw.conn, snapshot, schema=raw.schema)
    raw.conn.execute(
        sql.SQL(
            "update {} set seen_meshstl = true "
            "where snapshot_id = %s and decode_status = 'stripped'"
        ).format(message_table(raw)),
        (SNAPSHOT_ID,),
    )
    # Per hour against rows_with_flag, and per broker against stored plus merged_cross_broker.
    assert load.reconcile(raw.conn, snapshot, schema=raw.schema) == [
        "cross_broker: meshstl",
        "flagged: 2026-01-01T07 meshstl",
    ]


def test_no_stored_position_is_zero_zero(raw: ThrowawayRaw, snapshot: Path) -> None:
    # pos_03, no position at 0, 0 is used (Casey, 2026-11-14): the fixtures store none.
    load.load_snapshot(raw.conn, snapshot, schema=raw.schema)
    assert load.zero_positions(raw.conn, SNAPSHOT_ID, schema=raw.schema) == {}


def test_a_zero_zero_position_is_counted_per_flag_and_source(
    raw: ThrowawayRaw, snapshot: Path
) -> None:
    load.load_snapshot(raw.conn, snapshot, schema=raw.schema)
    raw.conn.execute(
        sql.SQL(
            "update {} set latitude_i = 0, longitude_i = 0 "
            "where snapshot_id = %s and position_source = 'position' and seen_meshstl"
        ).format(message_table(raw)),
        (SNAPSHOT_ID,),
    )
    found = load.zero_positions(raw.conn, SNAPSHOT_ID, schema=raw.schema)
    assert found == {"seen_meshstl, position": 2, "seen_public, position": 1}


def test_one_coordinate_at_zero_is_not_counted(raw: ThrowawayRaw, snapshot: Path) -> None:
    # pos_03 concerns a position at 0, 0: a latitude of 0 with a real longitude is a position.
    load.load_snapshot(raw.conn, snapshot, schema=raw.schema)
    raw.conn.execute(
        sql.SQL(
            "update {} set latitude_i = 0 where snapshot_id = %s and position_source = 'position'"
        ).format(message_table(raw)),
        (SNAPSHOT_ID,),
    )
    assert load.zero_positions(raw.conn, SNAPSHOT_ID, schema=raw.schema) == {}


def test_the_load_command_warns_about_positions_at_zero() -> None:
    with structlog.testing.capture_logs() as logs:
        load.warn_zero_positions(SNAPSHOT_ID, {"seen_public, position": 119})
        load.warn_zero_positions(SNAPSHOT_ID, {})
    assert [(entry["event"], entry["log_level"]) for entry in logs] == [
        ("pos_03_positions_at_zero", "warning")
    ]
    assert logs[0]["rows"] == {"seen_public, position": 119}


def test_decode_rate_per_broker_flag(raw: ThrowawayRaw, snapshot: Path) -> None:
    # ing_04: ok over stored radio deliveries on e topics that carried a payload. Public:
    # 14 of 24 (the 10 others are unknown_port, decrypt_failed and malformed fixtures).
    # MeshSTL: 3 of 4 (the wrong-key fixture fails).
    load.load_snapshot(raw.conn, snapshot, schema=raw.schema)
    rates = load.decode_rates(raw.conn, SNAPSHOT_ID, schema=raw.schema)
    assert rates == {"seen_public": 14 / 24, "seen_meshstl": 3 / 4}


def test_raw_content_hashes_equal_the_manifest(raw: ThrowawayRaw, snapshot: Path) -> None:
    # snp_01: float4 columns come back from the warehouse as the float32 the Parquet held.
    load.load_snapshot(raw.conn, snapshot, schema=raw.schema)
    hashes = load.content_hashes(raw.conn, SNAPSHOT_ID, schema=raw.schema)
    manifest = read_manifest(snapshot)["content_hashes"]
    assert {table: hashes[table] for table in manifest} == manifest
    assert load.hash_problems(raw.conn, snapshot, schema=raw.schema) == []


def test_the_load_command_fails_on_any_problem() -> None:
    with structlog.testing.capture_logs() as logs:
        assert load.report(SNAPSHOT_ID, {}, ["counted: 2026-01-01T07"], {}) == 1
        assert load.report(SNAPSHOT_ID, {}, [], {}) == 0
    assert [(entry["event"], entry["log_level"]) for entry in logs] == [
        ("load_problems", "error"),
        ("load_done", "info"),
    ]


def test_content_hashes_leave_out_registered_at(raw: ThrowawayRaw, snapshot: Path) -> None:
    load.load_snapshot(raw.conn, snapshot, schema=raw.schema)
    hashes = load.content_hashes(raw.conn, SNAPSHOT_ID, schema=raw.schema)
    raw.conn.execute("set local timezone = 'America/Chicago'")
    assert load.content_hashes(raw.conn, SNAPSHOT_ID, schema=raw.schema) == hashes
    assert "registered_at" not in load.SNAPSHOT_CONTENT_COLUMNS
