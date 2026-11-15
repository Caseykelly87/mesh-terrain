"""Capture snapshot folders: hour files and the capture manifest's fields and checks (ing_01).

The lifecycle every snapshot shares (`.partial`, manifest, finish, verify,
register) is tested in test_common_snapshot.py.
"""

from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from structlog.testing import capture_logs

from ingester.snapshot import REQUIRED_METADATA, flush_file, hour_file, write_manifest
from pipeline.common import snapshot as common
from pipeline.common.snapshot import SnapshotError, start_snapshot

SNAPSHOT_ID = "example-capture-2026-01-01"
HOUR = datetime(2026, 1, 1, 7, tzinfo=UTC)
METADATA = {
    "snapshot_id": SNAPSHOT_ID,
    "capture_date": "2026-01-01",
    "source_url": ["mqtt://broker.example.invalid"],
    "licence": "Invented licence line for a test.",
    "stop_reason": "end_at",
    "seeds": {},
    "content_hashes": {"mqtt_message": "0" * 64},
    "decoder_version": "1",
    "schema_version": 1,
    "brokers": ["broker.example.invalid"],
    "topics": ["msh/US/Example/#"],
    "end_at": "2026-01-02T00:00:00+00:00",
    "max_raw_bytes": 10_000_000_000,
    "deliveries": [],
}


def fill(folder: Path) -> None:
    path = hour_file(folder, "mqtt_copy_count", HOUR)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"rows")
    (folder / "README.md").write_text("what, when, where\n", encoding="utf-8")


def test_hour_and_flush_file_names() -> None:
    folder = Path("snap")
    assert hour_file(folder, "mqtt_message", HOUR) == folder / "mqtt_message/2026-01-01T07.parquet"
    assert flush_file(folder, "mqtt_copy_count", HOUR, 3) == (
        folder / "mqtt_copy_count/2026-01-01T07.flush-00003.parquet"
    )


def test_hour_file_rejects_a_time_that_is_not_an_utc_hour() -> None:
    with pytest.raises(ValueError):
        hour_file(Path("snap"), "mqtt_message", HOUR.replace(minute=1))


def test_the_capture_manifest_requires_every_shared_field() -> None:
    assert set(common.REQUIRED_METADATA) <= set(REQUIRED_METADATA)


def test_the_capture_manifest_keeps_its_run_fields(tmp_path: Path) -> None:
    partial = start_snapshot(tmp_path, SNAPSHOT_ID)
    fill(partial)
    write_manifest(partial, METADATA)
    manifest = common.read_manifest(partial)
    assert manifest["seeds"] == {}
    assert manifest["stop_reason"] == "end_at"


def test_manifest_requires_the_snp_01_and_run_fields(tmp_path: Path) -> None:
    partial = start_snapshot(tmp_path, SNAPSHOT_ID)
    required = (
        "licence",
        "seeds",
        "source_url",
        "content_hashes",
        "stop_reason",
        "brokers",
        "topics",
        "end_at",
        "max_raw_bytes",
        "deliveries",
    )
    for field in required:
        incomplete = {key: value for key, value in METADATA.items() if key != field}
        with pytest.raises(SnapshotError):
            write_manifest(partial, incomplete)


def test_manifest_refuses_unmerged_flush_files(tmp_path: Path) -> None:
    partial = start_snapshot(tmp_path, SNAPSHOT_ID)
    path = flush_file(partial, "mqtt_message", HOUR, 1)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"rows")
    with pytest.raises(SnapshotError):
        write_manifest(partial, METADATA)


def write_keys(folder: Path, hour: datetime, keys: list[str]) -> None:
    path = hour_file(folder, "mqtt_message", hour)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"delivery_key": keys}), path)


def test_manifest_refuses_a_delivery_key_in_two_hour_files(tmp_path: Path) -> None:
    # ing_01: one row per delivery key in the whole snapshot, not only per file.
    partial = start_snapshot(tmp_path, SNAPSHOT_ID)
    write_keys(partial, HOUR, ["a" * 64, "b" * 64])
    write_keys(partial, HOUR.replace(hour=8), ["b" * 64])
    with pytest.raises(SnapshotError):
        write_manifest(partial, METADATA)


def test_manifest_accepts_distinct_delivery_keys(tmp_path: Path) -> None:
    partial = start_snapshot(tmp_path, SNAPSHOT_ID)
    write_keys(partial, HOUR, ["a" * 64])
    write_keys(partial, HOUR.replace(hour=8), ["b" * 64])
    write_manifest(partial, METADATA)


def test_the_manifest_write_logs_the_delivery_rows(tmp_path: Path) -> None:
    partial = start_snapshot(tmp_path, SNAPSHOT_ID)
    write_keys(partial, HOUR, ["a" * 64, "b" * 64])
    with capture_logs() as logs:
        write_manifest(partial, METADATA)
    events = [entry["event"] for entry in logs]
    assert events == ["manifest_write_begin", "manifest_write_end"]
    assert logs[1]["delivery_rows"] == 2
    assert logs[1]["snapshot_id"] == SNAPSHOT_ID
