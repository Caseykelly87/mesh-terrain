"""A capture run on a fake clock: flush files, the merge at exit and the stop rules (ADR 0005)."""

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from ingester import capture
from ingester.capture import CaptureRun, RunParameters
from ingester.sightings import Broker
from ingester.snapshot import MANIFEST_NAME, read_manifest, verify_manifest

KEY = bytes(range(32))
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "mqtt" / "deliveries.jsonl"
START = datetime(2026, 1, 1, 7, tzinfo=UTC)
END_AT = datetime(2026, 1, 1, 9, tzinfo=UTC)


class FakeTime:
    """One fake clock for both the monotonic seconds and the wall time."""

    def __init__(self) -> None:
        self.at = START

    def monotonic(self) -> float:
        return (self.at - START).total_seconds()

    def now(self) -> datetime:
        return self.at


def params(**changes: Any) -> RunParameters:
    values: dict[str, Any] = {
        "host": "broker.example.invalid",
        "port": 1883,
        "source": Broker.PUBLIC,
        "topics": ["msh/US/Example/#"],
        "end_at": END_AT,
        "max_raw_bytes": 10_000_000_000,
        "snapshot_id": "example-capture-2026-01-01",
    }
    return RunParameters(**(values | changes))


def public_records() -> list[dict[str, str]]:
    lines = FIXTURES.read_text(encoding="utf-8").splitlines()
    return [record for record in map(json.loads, lines) if record["broker"] == "public"]


def run_through(
    tmp_path: Path, flush_every: int = 5, **changes: Any
) -> tuple[CaptureRun, FakeTime]:
    fake = FakeTime()
    run = CaptureRun(
        tmp_path, params(**changes), KEY, clock=fake.monotonic, now=fake.now, interval_s=300.0
    )
    for number, record in enumerate(public_records(), 1):
        fake.at = datetime.fromisoformat(record["received_at"])
        run.receive(record["topic"], base64.b64decode(record["payload_b64"]))
        if number % flush_every == 0:
            fake.at += timedelta(seconds=300)
            run.maybe_flush()
    return run, fake


def test_flush_files_appear_during_the_run(tmp_path: Path) -> None:
    run, _ = run_through(tmp_path)
    assert list(run.partial.rglob("*.flush-*.parquet"))
    assert not (run.partial / MANIFEST_NAME).exists()


def test_finish_merges_to_one_file_per_table_and_hour(tmp_path: Path) -> None:
    run, _ = run_through(tmp_path)
    folder = run.finish("end_at")
    assert folder == tmp_path / "example-capture-2026-01-01"
    assert not list(folder.rglob("*.flush-*"))
    names = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*.parquet"))
    assert names == [
        "mqtt_copy_count/2026-01-01T07.parquet",
        "mqtt_dropped_count/2026-01-01T07.parquet",
        "mqtt_message/2026-01-01T07.parquet",
    ]
    assert verify_manifest(folder) == []


def test_the_manifest_records_the_run_parameters_and_stop_reason(tmp_path: Path) -> None:
    run, _ = run_through(tmp_path)
    manifest = read_manifest(run.finish("max_raw_bytes"))
    assert manifest["stop_reason"] == "max_raw_bytes"
    assert manifest["end_at"] == END_AT.isoformat()
    assert manifest["max_raw_bytes"] == 10_000_000_000
    assert manifest["topics"] == {"public": ["msh/US/Example/#"]}
    assert manifest["snapshot_bytes"] > 0


def test_a_repeat_is_merged_and_the_hours_balance(tmp_path: Path) -> None:
    run, _ = run_through(tmp_path)
    folder = run.finish("end_at")
    deliveries = read_manifest(folder)["deliveries"]
    by_hour = {row["hour"]: row for row in deliveries}
    assert by_hour["2026-01-01T08"]["merged_repeat"] == 1
    for row in deliveries:
        parts = ("stored", "counted", "merged_cross_broker", "merged_repeat")
        assert row["received"] == sum(row[part] for part in parts)
    rows = pq.read_table(folder / "mqtt_message" / "2026-01-01T07.parquet").to_pylist()
    assert all(row["seen_public"] and not row["seen_meshstl"] for row in rows)
    assert len(rows) == sum(row["stored"] for row in deliveries)


def test_merged_counts_equal_the_flushed_counts(tmp_path: Path) -> None:
    run, _ = run_through(tmp_path, flush_every=2)
    folder = run.finish("end_at")
    copies = pq.read_table(folder / "mqtt_copy_count" / "2026-01-01T07.parquet").to_pylist()
    dropped = pq.read_table(folder / "mqtt_dropped_count" / "2026-01-01T07.parquet").to_pylist()
    counted = sum(row["counted"] for row in read_manifest(folder)["deliveries"])
    total = sum(row["copy_count"] for row in copies) + sum(r["delivery_count"] for r in dropped)
    assert total == counted


def test_nothing_after_end_at_is_kept(tmp_path: Path) -> None:
    fake = FakeTime()
    run = CaptureRun(tmp_path, params(), KEY, clock=fake.monotonic, now=fake.now)
    record = public_records()[0]
    fake.at = END_AT
    run.receive(record["topic"], base64.b64decode(record["payload_b64"]))
    assert run.received == 0


def test_stop_reason_on_the_fake_clock(tmp_path: Path) -> None:
    fake = FakeTime()
    run = CaptureRun(tmp_path, params(max_raw_bytes=1), KEY, clock=fake.monotonic, now=fake.now)
    assert run.stop_reason() is None
    (run.partial / "placeholder").write_bytes(b"xx")
    assert run.stop_reason() == "max_raw_bytes"
    fake.at = END_AT
    assert run.stop_reason() == "end_at"


def test_a_write_failure_is_retried_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run, _ = run_through(tmp_path, flush_every=1000)
    real = capture.write_rows
    calls = {"n": 0}

    def flaky(path: Path, table: str, rows: Any) -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk hiccup")
        return real(path, table, rows)

    monkeypatch.setattr(capture, "write_rows", flaky)
    run.flush()
    assert list(run.partial.rglob("*.flush-*.parquet"))


def test_a_second_write_failure_stops_without_a_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = run_through(tmp_path, flush_every=1000)

    def broken(path: Path, table: str, rows: Any) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(capture, "write_rows", broken)
    with pytest.raises(OSError):
        run.finish("end_at")
    assert not (run.partial / MANIFEST_NAME).exists()
    assert run.partial.exists()
