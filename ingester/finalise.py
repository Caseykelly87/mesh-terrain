"""Finish a snapshot folder: merge flush files, write coverage, README and manifest (ADR 0005).

Shared by the reduction and the capture command, so both finish a snapshot
the same way: each hour's flush files are merged into one file after the
merge check, then coverage.json, the README and the manifest are written and
the folder is renamed from `<id>.partial/` to `<id>/`.
"""

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from ingester.allowlist import DECODER_VERSION, SCHEMA_VERSION, allowlist
from ingester.flush import check_merged_counts, check_merged_keys
from ingester.parquet import content_hash, read_rows, write_rows
from ingester.readme import render_readme
from ingester.snapshot import finish_snapshot, flush_file, hour_file, write_manifest

log = structlog.get_logger(component="ingester.finalise")

DELIVERY_TABLE = "mqtt_message"
LANDED_TABLES = ("mqtt_message", "mqtt_copy_count", "mqtt_dropped_count")
COUNT_TABLES = {
    "mqtt_copy_count": (("hour", "topic", "gateway_pseudonym"), "copy_count"),
    "mqtt_dropped_count": (("hour", "topic_class", "reason"), "delivery_count"),
}


@dataclass(frozen=True)
class SnapshotInfo:
    """What the README and manifest say about the snapshot (snp_01)."""

    snapshot_id: str
    capture_date: str
    source_url: list[str]
    licence: str
    brokers: list[str]
    topics: dict[str, list[str]]


def _flush_paths(partial: Path, table: str, hour: datetime, flushes: int) -> list[Path]:
    return [flush_file(partial, table, hour, number) for number in range(1, flushes + 1)]


def merge_hour(partial: Path, hour: datetime, flushes: int) -> None:
    """Merge one hour's delivery flush files into its hour file, then remove them."""
    paths = _flush_paths(partial, DELIVERY_TABLE, hour, flushes)
    flushed = [read_rows(path) for path in paths]
    rows = sorted(
        (row for part in flushed for row in part),
        key=lambda row: (row["received_at"], row["delivery_key"]),
    )
    target = hour_file(partial, DELIVERY_TABLE, hour)
    write_rows(target, DELIVERY_TABLE, rows)
    merged_keys = [row["delivery_key"] for row in read_rows(target)]
    check_merged_keys([[row["delivery_key"] for row in part] for part in flushed], merged_keys)
    for path in paths:
        path.unlink()
    log.info("merge", table=DELIVERY_TABLE, hour=f"{hour:%Y-%m-%dT%H}", count=len(rows))


def _counts_by_key(rows: list[dict[str, Any]], table: str) -> Counter[tuple[Any, ...]]:
    key_columns, count_column = COUNT_TABLES[table]
    counts: Counter[tuple[Any, ...]] = Counter()
    for row in rows:
        counts[tuple(row[name] for name in key_columns)] += row[count_column]
    return counts


def merge_count_hour(partial: Path, table: str, hour: datetime, flushes: int) -> None:
    """Sum one hour's count flush files per key into its hour file, then remove them."""
    key_columns, count_column = COUNT_TABLES[table]
    paths = _flush_paths(partial, table, hour, flushes)
    flushed = [read_rows(path) for path in paths]
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in (row for part in flushed for row in part):
        key = tuple(row[name] for name in key_columns)
        if key in merged:
            merged[key][count_column] += row[count_column]
        else:
            merged[key] = dict(row)
    target = hour_file(partial, table, hour)
    write_rows(target, table, [merged[key] for key in sorted(merged)])
    check_merged_counts(
        [_counts_by_key(part, table) for part in flushed],
        _counts_by_key(read_rows(target), table),
    )
    for path in paths:
        path.unlink()
    log.info("merge", table=table, hour=f"{hour:%Y-%m-%dT%H}", count=len(merged))


Minutes = dict[int, tuple[float, float]]


def note_minute(minutes: Minutes, at_s: float) -> None:
    """Widen the first and last receipt of at_s's minute to include it."""
    minute = int(at_s // 60)
    low, high = minutes.get(minute, (at_s, at_s))
    minutes[minute] = (min(low, at_s), max(high, at_s))


def merge_minutes(into: Minutes, other: Minutes) -> None:
    """Add another set of minute bounds to into."""
    for minute, (low, high) in other.items():
        known = into.get(minute, (low, high))
        into[minute] = (min(known[0], low), max(known[1], high))


def _rows(minutes: Minutes) -> list[list[float]]:
    return [[minute, low, high] for minute, (low, high) in sorted(minutes.items())]


def write_coverage(
    partial: Path,
    minutes: dict[str, Minutes],
    root_minutes: dict[str, dict[str, Minutes]] | None = None,
) -> dict[str, float]:
    """coverage.json and coverage_roots.json; returns observed hours per broker (15-minute gaps).

    Args:
        partial: The `<id>.partial/` folder.
        minutes: Per broker, the first and last receipt of every minute.
        root_minutes: The same per broker and regional topic root (topics.COVERAGE_ROOTS).
    """
    coverage = {broker: _rows(by_minute) for broker, by_minute in sorted(minutes.items())}
    by_root = {
        broker: {root: _rows(by_minute) for root, by_minute in sorted(roots.items())}
        for broker, roots in sorted((root_minutes or {}).items())
        if roots
    }
    for name, content in (("coverage.json", coverage), ("coverage_roots.json", by_root)):
        text = json.dumps(content, separators=(",", ":")) + "\n"
        (partial / name).write_text(text, encoding="utf-8")
    return {broker: observed_hours(rows) for broker, rows in coverage.items()}


def observed_hours(minutes: list[list[float]], gap_s: float = 900.0) -> float:
    """Sum of segment spans in hours; a gap over gap_s starts a new segment (ADR 0003 spike)."""
    total_s, start, end = 0.0, None, None
    for _, low, high in minutes:
        if end is not None and low - end <= gap_s:
            end = high
            continue
        if start is not None and end is not None:
            total_s += end - start
        start, end = low, high
    if start is not None and end is not None:
        total_s += end - start
    return total_s / 3600.0


def write_metadata(
    partial: Path,
    info: SnapshotInfo,
    deliveries: list[dict[str, str | int]],
    hours: dict[str, float],
    run: dict[str, Any],
) -> Path:
    """README, content hashes and the manifest.

    Args:
        partial: The `<id>.partial/` folder with every table written.
        info: The snapshot's id, date, URLs, licence, brokers and topics.
        deliveries: Per hour and broker counts from BrokerHourCounts.rows().
        hours: Observed hours per broker.
        run: end_at, max_raw_bytes, stop_reason and anything else the run records.
    """
    hashes = {
        table: content_hash(sorted((partial / table).glob("*.parquet")), allowlist(table))
        for table in LANDED_TABLES
    }
    metadata: dict[str, Any] = {
        **vars(info),
        "seeds": {},
        "content_hashes": hashes,
        "deliveries": deliveries,
        "decoder_version": DECODER_VERSION,
        "schema_version": SCHEMA_VERSION,
        "observed_hours": hours,
        **run,
    }
    (partial / "README.md").write_text(render_readme(metadata), encoding="utf-8")
    return write_manifest(partial, metadata)


def finish(partial: Path) -> Path:
    """Rename the folder once its manifest exists."""
    return finish_snapshot(partial)
