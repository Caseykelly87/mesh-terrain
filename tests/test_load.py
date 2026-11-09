"""Loading a snapshot into raw (ing_01, ing_03, ing_04, ing_05, ing_07, snp_01).

The database tests run inside a transaction that is rolled back, so they
leave nothing in the warehouse. They need the local stack (`make up`); where
no warehouse answers, as in CI's tests job, they are skipped and say so.
"""

import re
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from test_reduce import INFO, KEY, SNAPSHOT_ID, table_rows, write_archive

from ingester import load
from ingester.reduce import reduce_capture
from ingester.snapshot import SnapshotError
from pipeline.common.settings import get_settings

LOADER_SOURCE = Path(load.__file__).read_text(encoding="utf-8")


def connect() -> psycopg.Connection:
    settings = get_settings()
    return psycopg.connect(
        host=settings.warehouse_host,
        port=settings.warehouse_port,
        user=settings.warehouse_user,
        password=settings.warehouse_password.get_secret_value(),
        dbname=settings.warehouse_db,
        connect_timeout=3,
    )


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    try:
        connection = connect()
    except psycopg.OperationalError as err:
        pytest.skip(f"no local warehouse ({err.__class__.__name__}); run make up")
    with connection, connection.transaction(force_rollback=True):
        yield connection


@pytest.fixture
def snapshot(tmp_path: Path) -> Path:
    sources = write_archive(tmp_path / "archive")
    return reduce_capture(sources, tmp_path / "snapshots", INFO, KEY, workers=1)


def test_the_loader_never_updates_or_deletes() -> None:
    # ing_07: rows in raw are only ever inserted.
    assert not re.search(r"\b(update|delete|truncate|drop)\b", LOADER_SOURCE, re.IGNORECASE)


def test_a_partial_folder_is_refused(tmp_path: Path) -> None:
    (tmp_path / f"{SNAPSHOT_ID}.partial").mkdir()
    with pytest.raises(SnapshotError):
        load.snapshot_folder(tmp_path, SNAPSHOT_ID)
    with pytest.raises(SnapshotError):
        load.snapshot_folder(tmp_path, f"{SNAPSHOT_ID}.partial")


def test_a_folder_without_a_manifest_is_refused(tmp_path: Path) -> None:
    (tmp_path / SNAPSHOT_ID).mkdir()
    with pytest.raises(SnapshotError):
        load.snapshot_folder(tmp_path, SNAPSHOT_ID)


def test_load_inserts_every_row_once(conn: psycopg.Connection, snapshot: Path) -> None:
    inserted = load.load_snapshot(conn, snapshot)
    for table in ("mqtt_message", "mqtt_copy_count", "mqtt_dropped_count"):
        assert inserted[table] == len(table_rows(snapshot, table)), table
    assert inserted["snapshot"] == 1
    assert load.row_counts(conn, SNAPSHOT_ID)["mqtt_message"] == inserted["mqtt_message"]


def test_a_second_load_changes_nothing(conn: psycopg.Connection, snapshot: Path) -> None:
    load.load_snapshot(conn, snapshot)
    before = load.row_counts(conn, SNAPSHOT_ID)
    again = load.load_snapshot(conn, snapshot)
    assert set(again.values()) == {0}
    assert load.row_counts(conn, SNAPSHOT_ID) == before


def test_a_changed_file_stops_the_load_before_any_insert(
    conn: psycopg.Connection, snapshot: Path
) -> None:
    path = sorted((snapshot / "mqtt_copy_count").glob("*.parquet"))[0]
    path.write_bytes(path.read_bytes() + b"\x00")
    with pytest.raises(SnapshotError):
        load.load_snapshot(conn, snapshot)


def test_schema_drift_is_refused(conn: psycopg.Connection, snapshot: Path) -> None:
    load.apply_ddl(conn)
    conn.execute("alter table raw.mqtt_message add column drift_test integer")
    with pytest.raises(load.SchemaDriftError):
        load.load_snapshot(conn, snapshot)


def test_reconciliation_passes_on_a_loaded_snapshot(
    conn: psycopg.Connection, snapshot: Path
) -> None:
    load.load_snapshot(conn, snapshot)
    assert load.reconcile(conn, snapshot) == []


def test_decode_rate_per_broker_flag(conn: psycopg.Connection, snapshot: Path) -> None:
    # ing_04: ok over stored radio deliveries that carried a payload.
    load.load_snapshot(conn, snapshot)
    rates = load.decode_rates(conn, SNAPSHOT_ID)
    assert set(rates) == {"seen_public", "seen_meshstl"}
    assert all(0.0 <= rate <= 1.0 for rate in rates.values())


def test_content_hashes_leave_out_registered_at(conn: psycopg.Connection, snapshot: Path) -> None:
    load.load_snapshot(conn, snapshot)
    hashes = load.content_hashes(conn, SNAPSHOT_ID)
    conn.execute("set local timezone = 'America/Chicago'")
    assert load.content_hashes(conn, SNAPSHOT_ID) == hashes
    assert "registered_at" not in load.SNAPSHOT_CONTENT_COLUMNS
