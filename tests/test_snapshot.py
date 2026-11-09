"""Snapshot folder lifecycle and manifest (ADR 0005 items 1 and 6, ing_02, snp_01)."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from structlog.testing import capture_logs

from ingester.snapshot import (
    MANIFEST_NAME,
    PartialExistsError,
    SnapshotError,
    SnapshotExistsError,
    finish_snapshot,
    finished_snapshots,
    flush_file,
    hour_file,
    partial_folders,
    start_snapshot,
    verify_manifest,
    write_manifest,
)

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
    path = hour_file(folder, "mqtt_message", HOUR)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"rows")
    (folder / "README.md").write_text("what, when, where\n", encoding="utf-8")


def test_start_creates_the_partial_folder(tmp_path: Path) -> None:
    partial = start_snapshot(tmp_path, SNAPSHOT_ID)
    assert partial == tmp_path / f"{SNAPSHOT_ID}.partial"
    assert partial.is_dir()


def test_start_refuses_when_the_snapshot_exists(tmp_path: Path) -> None:
    (tmp_path / SNAPSHOT_ID).mkdir()
    with pytest.raises(SnapshotExistsError):
        start_snapshot(tmp_path, SNAPSHOT_ID)


def test_start_refuses_and_keeps_an_earlier_partial_folder(tmp_path: Path) -> None:
    # Nothing deletes a .partial folder automatically; Casey decides (snp_01).
    earlier = tmp_path / f"{SNAPSHOT_ID}.partial"
    earlier.mkdir()
    (earlier / "left-over").write_bytes(b"x")
    with pytest.raises(PartialExistsError):
        start_snapshot(tmp_path, SNAPSHOT_ID)
    assert (earlier / "left-over").exists()


@pytest.mark.parametrize("bad", ["", "x.partial", "../escape", "a/b", "Upper", "-lead"])
def test_start_rejects_bad_ids(tmp_path: Path, bad: str) -> None:
    with pytest.raises(SnapshotError):
        start_snapshot(tmp_path, bad)


def test_hour_and_flush_file_names() -> None:
    folder = Path("snap")
    assert hour_file(folder, "mqtt_message", HOUR) == folder / "mqtt_message/2026-01-01T07.parquet"
    assert flush_file(folder, "mqtt_copy_count", HOUR, 3) == (
        folder / "mqtt_copy_count/2026-01-01T07.flush-00003.parquet"
    )


def test_hour_file_rejects_a_time_that_is_not_an_utc_hour() -> None:
    with pytest.raises(ValueError):
        hour_file(Path("snap"), "mqtt_message", HOUR.replace(minute=1))


def test_manifest_lists_every_file_with_sha256_and_size(tmp_path: Path) -> None:
    partial = start_snapshot(tmp_path, SNAPSHOT_ID)
    fill(partial)
    manifest = json.loads(write_manifest(partial, METADATA).read_text(encoding="utf-8"))
    paths = [entry["path"] for entry in manifest["files"]]
    assert paths == ["README.md", "mqtt_message/2026-01-01T07.parquet"]
    rows = next(entry for entry in manifest["files"] if entry["path"].endswith(".parquet"))
    assert rows["sha256"] == "bc51e9e65d79e1c90ba544ab2eae1044ccdf8c1af7a559ba375cba4276f7d99e"
    assert rows["bytes"] == 4
    assert manifest["snapshot_bytes"] == sum(entry["bytes"] for entry in manifest["files"])
    assert manifest["seeds"] == {}
    assert manifest["stop_reason"] == "end_at"


def test_manifest_requires_the_snp_01_fields(tmp_path: Path) -> None:
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


def test_manifest_refuses_a_different_snapshot_id(tmp_path: Path) -> None:
    partial = start_snapshot(tmp_path, SNAPSHOT_ID)
    with pytest.raises(SnapshotError):
        write_manifest(partial, {**METADATA, "snapshot_id": "another-2026-01-01"})


def test_manifest_refuses_unmerged_flush_files(tmp_path: Path) -> None:
    partial = start_snapshot(tmp_path, SNAPSHOT_ID)
    path = flush_file(partial, "mqtt_message", HOUR, 1)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"rows")
    with pytest.raises(SnapshotError):
        write_manifest(partial, METADATA)


def test_finish_renames_only_after_the_manifest(tmp_path: Path) -> None:
    partial = start_snapshot(tmp_path, SNAPSHOT_ID)
    fill(partial)
    with pytest.raises(SnapshotError):
        finish_snapshot(partial)
    write_manifest(partial, METADATA)
    final = finish_snapshot(partial)
    assert final == tmp_path / SNAPSHOT_ID
    assert (final / MANIFEST_NAME).exists()
    assert not partial.exists()


def test_verify_passes_on_an_untouched_snapshot(tmp_path: Path) -> None:
    partial = start_snapshot(tmp_path, SNAPSHOT_ID)
    fill(partial)
    write_manifest(partial, METADATA)
    assert verify_manifest(finish_snapshot(partial)) == []


def test_verify_names_changed_missing_and_extra_files(tmp_path: Path) -> None:
    partial = start_snapshot(tmp_path, SNAPSHOT_ID)
    fill(partial)
    write_manifest(partial, METADATA)
    final = finish_snapshot(partial)
    hour_file(final, "mqtt_message", HOUR).write_bytes(b"changed")
    (final / "README.md").unlink()
    (final / "extra.parquet").write_bytes(b"x")
    problems = verify_manifest(final)
    assert problems == [
        "changed: mqtt_message/2026-01-01T07.parquet",
        "extra: extra.parquet",
        "missing: README.md",
    ]


def test_verify_refuses_a_folder_without_a_manifest(tmp_path: Path) -> None:
    partial = start_snapshot(tmp_path, SNAPSHOT_ID)
    with pytest.raises(SnapshotError):
        verify_manifest(partial)


def test_partial_and_finished_folders_are_listed_apart(tmp_path: Path) -> None:
    done = start_snapshot(tmp_path, SNAPSHOT_ID)
    fill(done)
    write_manifest(done, METADATA)
    finish_snapshot(done)
    start_snapshot(tmp_path, "example-capture-2026-01-02")
    assert partial_folders(tmp_path) == [tmp_path / "example-capture-2026-01-02.partial"]
    assert finished_snapshots(tmp_path) == [tmp_path / SNAPSHOT_ID]


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


def test_filesystem_operations_log_start_and_end_with_the_snapshot_id(tmp_path: Path) -> None:
    with capture_logs() as logs:
        partial = start_snapshot(tmp_path, SNAPSHOT_ID)
        fill(partial)
        write_manifest(partial, METADATA)
        verify_manifest(finish_snapshot(partial))
    events = [(entry["event"], entry.get("snapshot_id")) for entry in logs]
    for name in ("snapshot_start", "manifest_write", "snapshot_finish", "manifest_verify"):
        assert (f"{name}_begin", SNAPSHOT_ID) in events
        assert (f"{name}_end", SNAPSHOT_ID) in events
    manifest_end = next(entry for entry in logs if entry["event"] == "manifest_write_end")
    assert manifest_end["files"] == 2
    assert manifest_end["bytes"] > 0
    assert manifest_end["duration_s"] >= 0
