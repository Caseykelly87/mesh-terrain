"""The batch reduction on a small archive built from the fixtures (ADR 0005, ing_01, ing_03)."""

import gzip
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from ingester.allowlist import TABLES, arrow_schema
from ingester.parquet import content_hash
from ingester.reduce import LICENCE, SnapshotInfo, Source, ordered_results, reduce_capture
from ingester.sightings import Broker
from ingester.snapshot import (
    MANIFEST_NAME,
    PartialExistsError,
    SnapshotExistsError,
    read_manifest,
    verify_manifest,
)

KEY = bytes(range(32))
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "mqtt" / "deliveries.jsonl"
NODE_PATTERN = re.compile(r"![0-9a-f]{8}")
SNAPSHOT_ID = "example-capture-2026-01-01"
WINDOW = (datetime(2026, 1, 1, 7, tzinfo=UTC), datetime(2026, 1, 1, 9, tzinfo=UTC))
INFO = SnapshotInfo(
    snapshot_id=SNAPSHOT_ID,
    capture_date="2026-01-01",
    source_url=["mqtt://public.example.invalid:1883", "mqtt://regional.example.invalid:1883"],
    licence=LICENCE,
    brokers=["public.example.invalid", "regional.example.invalid"],
    topics={"public": ["msh/US/#"], "meshstl": ["msh/US/Example/#"]},
)


def fixture_records() -> list[dict[str, str]]:
    return [json.loads(line) for line in FIXTURES.read_text(encoding="utf-8").splitlines()]


def write_archive(
    root: Path, outside_window: bool = False, regional_record: bool = False
) -> list[Source]:
    """Public hours gzipped, MeshSTL hours plain, as the spike stored them."""
    public, meshstl = root / "public", root / "meshstl"
    public.mkdir(parents=True)
    meshstl.mkdir(parents=True)
    by_file: dict[Path, list[str]] = {}
    records = fixture_records()
    if outside_window:
        late = dict(records[0], broker="meshstl", received_at="2026-01-01T09:00:00+00:00")
        records.append(late)
    if regional_record:
        topic = records[0]["topic"].replace("msh/US/Example/", "msh/US/MO/")
        records.append(dict(records[0], topic=topic, received_at="2026-01-01T07:20:00+00:00"))
    for record in records:
        folder = public if record["broker"] == "public" else meshstl
        hour = record["received_at"][:13]
        name = f"capture-{hour}.jsonl" + (".gz" if folder is public else "")
        line = json.dumps({key: record[key] for key in ("received_at", "topic", "payload_b64")})
        by_file.setdefault(folder / name, []).append(line + "\n")
    # A line cut short when a capture stopped, as at the end of the public capture.
    by_file[public / "capture-2026-01-01T08.jsonl.gz"].append('{"received_at": "2026-01-01T08:5')
    for path, lines in by_file.items():
        text = "".join(lines)
        if path.suffix == ".gz":
            path.write_bytes(gzip.compress(text.encode("ascii"), mtime=0))
        else:
            path.write_text(text, encoding="ascii")
    return [Source(Broker.PUBLIC, public), Source(Broker.MESHSTL, meshstl, *WINDOW)]


def reduce_fixtures(tmp_path: Path, outside_window: bool = False) -> Path:
    sources = write_archive(tmp_path / "archive", outside_window)
    return reduce_capture(sources, tmp_path / "snapshots", INFO, KEY, workers=1)


def table_rows(folder: Path, table: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((folder / table).glob("*.parquet")):
        rows += pq.read_table(path).to_pylist()
    return rows


def test_the_snapshot_is_final_and_matches_its_manifest(tmp_path: Path) -> None:
    folder = reduce_fixtures(tmp_path)
    assert folder == tmp_path / "snapshots" / SNAPSHOT_ID
    assert verify_manifest(folder) == []
    manifest = read_manifest(folder)
    assert manifest["stop_reason"] == "reduction_complete"
    assert manifest["end_at"] is None and manifest["max_raw_bytes"] is None
    assert manifest["licence"] == LICENCE
    assert LICENCE in (folder / "README.md").read_text(encoding="utf-8")


def test_each_parquet_schema_equals_the_allowlist(tmp_path: Path) -> None:
    # prv_01: the landed schema is the allowlist for its schema_version.
    folder = reduce_fixtures(tmp_path)
    for table in TABLES:
        paths = sorted((folder / table).glob("*.parquet"))
        assert paths, table
        for path in paths:
            assert pq.read_schema(path).remove_metadata() == arrow_schema(table)


def test_one_file_per_table_and_hour_and_no_flush_files(tmp_path: Path) -> None:
    folder = reduce_fixtures(tmp_path)
    names = sorted(path.name for path in (folder / "mqtt_message").iterdir())
    assert names == ["2026-01-01T07.parquet"]
    assert not list(folder.rglob("*.flush-*"))


def test_a_delivery_from_both_brokers_is_one_row_with_both_flags(tmp_path: Path) -> None:
    rows = table_rows(reduce_fixtures(tmp_path), "mqtt_message")
    keys = Counter(row["delivery_key"] for row in rows)
    assert max(keys.values()) == 1
    both = [row for row in rows if row["seen_public"] and row["seen_meshstl"]]
    assert len(both) == 1
    assert both[0]["received_at"] == datetime(2026, 1, 1, 7, 30, tzinfo=UTC)


def test_stripped_on_public_and_whole_on_meshstl_are_two_rows(tmp_path: Path) -> None:
    rows = table_rows(reduce_fixtures(tmp_path), "mqtt_message")
    late = [row for row in rows if row["received_at"].minute == 50]
    flags = sorted((row["decode_status"], row["seen_public"], row["seen_meshstl"]) for row in late)
    assert flags == [("ok", False, True), ("stripped", True, False)]


def test_deliveries_balance_per_hour_and_broker(tmp_path: Path) -> None:
    # ing_03 as amended: received = stored + counted + merged, per hour and broker.
    folder = reduce_fixtures(tmp_path)
    deliveries = read_manifest(folder)["deliveries"]
    received = Counter(
        (record["received_at"][:13], record["broker"]) for record in fixture_records()
    )
    assert {(row["hour"], row["broker"]): row["received"] for row in deliveries} == received
    for row in deliveries:
        parts = ("stored", "counted", "merged_cross_broker", "merged_repeat")
        assert row["received"] == sum(row[part] for part in parts)
    merged = {(row["hour"], row["broker"]): row for row in deliveries}
    assert merged[("2026-01-01T07", "meshstl")]["merged_cross_broker"] == 1
    assert merged[("2026-01-01T08", "public")]["merged_repeat"] == 1
    stored = sum(row["stored"] for row in deliveries)
    assert stored == len(table_rows(folder, "mqtt_message"))
    counted = sum(row["counted"] for row in deliveries)
    copies = sum(row["copy_count"] for row in table_rows(folder, "mqtt_copy_count"))
    dropped = sum(row["delivery_count"] for row in table_rows(folder, "mqtt_dropped_count"))
    assert counted == copies + dropped


def test_the_manifest_reconciles_per_broker(tmp_path: Path) -> None:
    # ing_03 (Casey, 2026-11-13): received, stored, counted and merged per broker balance,
    # and received equals the deliveries each broker sent.
    folder = reduce_fixtures(tmp_path)
    totals: dict[str, Counter[str]] = {"public": Counter(), "meshstl": Counter()}
    for row in read_manifest(folder)["deliveries"]:
        totals[row["broker"]].update(
            {name: row[name] for name in row if name not in {"hour", "broker"}}
        )
    sent = Counter(record["broker"] for record in fixture_records())
    for broker, found in totals.items():
        assert found["received"] == sent[broker], broker
        parts = ("stored", "counted", "merged_cross_broker", "merged_repeat")
        assert found["received"] == sum(found[part] for part in parts), broker
    assert totals["meshstl"]["merged_cross_broker"] == 1
    assert totals["public"]["merged_repeat"] == 1


def test_rows_with_each_flag_are_recorded_per_hour_and_broker(tmp_path: Path) -> None:
    # The loader's ing_03 check compares raw rows with each broker flag against these.
    folder = reduce_fixtures(tmp_path)
    found: Counter[tuple[str, str]] = Counter()
    for row in table_rows(folder, "mqtt_message"):
        hour = f"{row['received_at']:%Y-%m-%dT%H}"
        found[(hour, "public")] += int(row["seen_public"])
        found[(hour, "meshstl")] += int(row["seen_meshstl"])
    deliveries = read_manifest(folder)["deliveries"]
    recorded = {(row["hour"], row["broker"]): row["rows_with_flag"] for row in deliveries}
    assert {key: count for key, count in recorded.items() if count} == +found


def test_the_portnum_rule_count_is_in_the_manifest(tmp_path: Path) -> None:
    # Two public fixtures decrypt to portnum 0 and to one above the enum's maximum.
    rule = read_manifest(reduce_fixtures(tmp_path))["decrypt_failed_portnum_rule"]
    assert rule == {
        "deliveries": {"public": 2, "meshstl": 0},
        "rows": {"seen_public": 2, "seen_meshstl": 0},
    }


def test_two_workers_give_the_same_snapshot(tmp_path: Path) -> None:
    sources = write_archive(tmp_path / "archive")
    one = reduce_capture(sources, tmp_path / "one", INFO, KEY, workers=1)
    two = reduce_capture(sources, tmp_path / "two", INFO, KEY, workers=2)
    first, second = read_manifest(one), read_manifest(two)
    for name in ("content_hashes", "deliveries", "observed_hours", "decrypt_failed_portnum_rule"):
        assert first[name] == second[name], name


def test_groups_in_flight_are_bounded_and_come_back_in_order() -> None:
    submitted: list[int] = []

    class CountingPool(ThreadPoolExecutor):
        def submit(self, fn: Any, /, *args: Any, **kwargs: Any) -> Any:
            submitted.append(args[0])
            return super().submit(fn, *args, **kwargs)

    groups = [[number] for number in range(10)]
    with CountingPool(4) as pool:
        for taken, results in enumerate(ordered_results(pool, abs, groups, limit=3), 1):
            assert results == [taken - 1]
            assert len(submitted) <= taken + 3


def test_unreadable_lines_are_recorded_not_counted_as_deliveries(tmp_path: Path) -> None:
    manifest = read_manifest(reduce_fixtures(tmp_path))
    assert manifest["unreadable_lines"] == {"public": 1, "meshstl": 0}


def test_meshstl_records_outside_the_window_are_not_read(tmp_path: Path) -> None:
    folder = reduce_fixtures(tmp_path, outside_window=True)
    hours = {(row["hour"], row["broker"]) for row in read_manifest(folder)["deliveries"]}
    assert ("2026-01-01T09", "meshstl") not in hours
    assert read_manifest(folder)["outside_window"] == {"public": 0, "meshstl": 1}


def test_no_stored_string_holds_a_node_id(tmp_path: Path) -> None:
    folder = reduce_fixtures(tmp_path)
    for table in TABLES:
        for row in table_rows(folder, table):
            for value in row.values():
                items = value if isinstance(value, list) else [value]
                for item in items:
                    assert not isinstance(item, bytes)
                    if isinstance(item, str):
                        assert NODE_PATTERN.search(item) is None


def test_coverage_holds_receipt_minutes_per_broker(tmp_path: Path) -> None:
    coverage = json.loads((reduce_fixtures(tmp_path) / "coverage.json").read_text("utf-8"))
    assert set(coverage) == {"public", "meshstl"}
    first_minute = int(datetime(2026, 1, 1, 7, tzinfo=UTC).timestamp() // 60)
    assert coverage["public"][0][0] == first_minute


def test_coverage_of_each_regional_root_is_recorded(tmp_path: Path) -> None:
    # analyze.py's --roots runs count observed time from records under those roots only.
    sources = write_archive(tmp_path / "archive", regional_record=True)
    folder = reduce_capture(sources, tmp_path / "snapshots", INFO, KEY, workers=1)
    roots = json.loads((folder / "coverage_roots.json").read_text("utf-8"))
    at_s = datetime(2026, 1, 1, 7, 20, tzinfo=UTC).timestamp()
    assert roots == {"public": {"MO": [[int(at_s // 60), at_s, at_s]]}}


def test_content_hashes_are_in_the_manifest_and_reproduce(tmp_path: Path) -> None:
    first = reduce_fixtures(tmp_path / "one")
    second = reduce_fixtures(tmp_path / "two")
    hashes = read_manifest(first)["content_hashes"]
    assert hashes == read_manifest(second)["content_hashes"]
    paths = sorted((first / "mqtt_message").glob("*.parquet"))
    assert hashes["mqtt_message"] == content_hash(paths, [c.name for c in TABLES["mqtt_message"]])


def test_refuses_when_the_snapshot_or_a_partial_folder_exists(tmp_path: Path) -> None:
    folder = reduce_fixtures(tmp_path)
    sources = [Source(Broker.PUBLIC, tmp_path / "archive" / "public")]
    with pytest.raises(SnapshotExistsError):
        reduce_capture(sources, tmp_path / "snapshots", INFO, KEY, workers=1)
    other = tmp_path / "other"
    (other / f"{SNAPSHOT_ID}.partial").mkdir(parents=True)
    with pytest.raises(PartialExistsError):
        reduce_capture(sources, other, INFO, KEY, workers=1)
    assert (folder / MANIFEST_NAME).exists()
