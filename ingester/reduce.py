"""Reduce the 2026-10-22 real-id capture archive into a pseudonymised snapshot (ADR 0005).

Reads the public capture (gzipped hourly JSON lines) and the MeshSTL capture
(plain hourly JSON lines, only its agreed window), decodes every delivery with
ingester.decoder, merges deliveries that share a delivery key in received_at
order across both brokers, and writes `<root>/<id>.partial/` with one zstd
Parquet file per table and hour, `coverage.json` (the first and last receipt
of every minute per broker, from which observed hours are computed), a README
and the sha256 manifest, then renames the folder. It refuses when `<id>/` or
`<id>.partial/` exists. It has no END_AT or MAX_RAW_BYTES: those are capture
parameters, and the manifest records them as null with the stop reason
`reduction_complete`.

Rows whose key MeshSTL also delivered are held back until every file is read,
so their broker flags are final before they are written; every other row is
public-only and is written as a flush file when its hour is done. Each hour's
flush files are merged into one file after the merge check (ADR 0005 item 6).

Run with `python -I -m ingester.reduce` so nothing in the archive folders can
shadow a module. It prints and logs counts only: never a topic, id or payload.
"""

import argparse
import base64
import gzip
import heapq
import json
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from ingester.allowlist import DECODER_VERSION, SCHEMA_VERSION
from ingester.counts import BrokerHourCounts, CopyCounts, DroppedCounts, hour_of
from ingester.decoder import CopyDelivery, StoredDelivery, decode_delivery
from ingester.finalise import (
    DELIVERY_TABLE,
    SnapshotInfo,
    finish,
    merge_hour,
    write_coverage,
    write_metadata,
)
from ingester.keys import delivery_key
from ingester.parquet import write_rows
from ingester.settings import get_ingester_settings
from ingester.sightings import Broker, SightingIndex
from ingester.snapshot import flush_file, hour_file, sha256_file, start_snapshot

__all__ = ["DEFAULT_INFO", "LICENCE", "SnapshotInfo", "Source", "reduce_capture"]

log = structlog.get_logger(component="ingester.reduce")

LICENCE = (
    "No licence published. Captured from the public Meshtastic broker and the MeshSTL "
    "regional broker under their terms of use, held privately and never redistributed. "
    "Only aggregated, pseudonymised derivatives are published."
)
SNAPSHOT_ID = "meshtastic-capture-2026-10-22"
# The MeshSTL capture's agreed 24 hours: 2026-10-23T22:09:00Z to 2026-10-24T22:08:59Z.
MESHSTL_WINDOW = (
    datetime(2026, 10, 23, 22, 9, tzinfo=UTC),
    datetime(2026, 10, 24, 22, 9, tzinfo=UTC),
)
PUBLIC_DIR = Path("C:/Users/Casey/mesh-terrain-data/spike-capture")
MESHSTL_DIR = Path("C:/Users/Casey/mesh-terrain-data/meshstl-capture")


@dataclass(frozen=True)
class Source:
    """One broker's capture folder and the window [start, end) of it that is read."""

    broker: Broker
    directory: Path
    start: datetime | None = None
    end: datetime | None = None

    def keeps(self, received_at: datetime) -> bool:
        """True when received_at falls in the window."""
        after_start = self.start is None or received_at >= self.start
        return after_start and (self.end is None or received_at < self.end)

    def files(self) -> dict[str, Path]:
        """Hourly capture files by hour stamp; public hours are gzipped, MeshSTL hours plain."""
        pattern = "capture-*.jsonl.gz" if self.broker is Broker.PUBLIC else "capture-*.jsonl"
        return {
            path.name.removeprefix("capture-").split(".")[0]: path
            for path in sorted(self.directory.glob(pattern))
        }


DEFAULT_INFO = SnapshotInfo(
    snapshot_id=SNAPSHOT_ID,
    capture_date="2026-10-22",
    source_url=["mqtt://mqtt.meshtastic.org:1883", "mqtt://mqtt.meshstl.org:1883"],
    licence=LICENCE,
    brokers=["mqtt.meshtastic.org", "mqtt.meshstl.org"],
    topics={"public": ["msh/US/#"], "meshstl": ["msh/US/MeshSTL/#"]},
)


@dataclass
class FileResult:
    """What one worker read from one capture file."""

    hour: str
    broker: Broker
    rows: list[tuple[datetime, str, dict[str, Any]]] = field(default_factory=list)
    copies: CopyCounts = field(default_factory=CopyCounts)
    dropped: DroppedCounts = field(default_factory=DroppedCounts)
    counted: BrokerHourCounts = field(default_factory=BrokerHourCounts)
    minutes: dict[int, tuple[float, float]] = field(default_factory=dict)
    statuses: Counter[str] = field(default_factory=Counter)
    unreadable: int = 0
    outside: int = 0
    source: dict[str, Any] = field(default_factory=dict)


_WORKER_KEY: bytes = b""


def _set_worker_key(key: bytes) -> None:
    global _WORKER_KEY
    _WORKER_KEY = key


def read_lines(path: Path) -> Iterator[str]:
    """Lines of a capture file, gzipped or plain."""
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="ascii") as handle:
            yield from handle
    else:
        with path.open(encoding="ascii") as handle:
            yield from handle


def read_records(path: Path, result: FileResult) -> Iterator[tuple[datetime, str, bytes]]:
    """(received_at, topic, payload) per record; a line that is not JSON is counted and skipped."""
    for line in read_lines(path):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            result.unreadable += 1
            continue
        received_at = datetime.fromisoformat(record["received_at"]).astimezone(UTC)
        yield received_at, record["topic"], base64.b64decode(record["payload_b64"])


def _note_minute(result: FileResult, received_at: datetime) -> None:
    at_s = received_at.timestamp()
    minute = int(at_s // 60)
    low, high = result.minutes.get(minute, (at_s, at_s))
    result.minutes[minute] = (min(low, at_s), max(high, at_s))


def decode_file(task: tuple[str, Source, Path]) -> FileResult:
    """Decode every in-window record of one capture file (runs in a worker process)."""
    hour, source, path = task
    result = FileResult(hour, source.broker)
    result.source = {"name": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
    for received_at, topic, payload in read_records(path, result):
        if not source.keeps(received_at):
            result.outside += 1
            continue
        _note_minute(result, received_at)
        decoded = decode_delivery(topic, payload, received_at, _WORKER_KEY)
        if isinstance(decoded, StoredDelivery):
            result.rows.append((received_at, source.broker.value, decoded.row))
            result.statuses[decoded.row["decode_status"]] += 1
            continue
        result.counted.add(received_at, source.broker, "counted")
        if isinstance(decoded, CopyDelivery):
            result.copies.add(
                received_at, decoded.topic, decoded.gateway_pseudonym, decoded.gateway_low_byte
            )
            result.statuses["internet_copy"] += 1
        else:
            result.dropped.add(received_at, decoded.topic_class, decoded.reason)
            result.statuses[decoded.reason] += 1
    result.rows.sort(key=lambda item: item[0])
    return result


def meshstl_keys(sources: list[Source], key: bytes) -> set[str]:
    """Delivery keys of every in-window MeshSTL record, so shared rows can be held back."""
    keys: set[str] = set()
    for source in sources:
        if source.broker is not Broker.MESHSTL:
            continue
        for path in source.files().values():
            scratch = FileResult("", source.broker)
            for received_at, topic, payload in read_records(path, scratch):
                if source.keeps(received_at):
                    keys.add(delivery_key(key, topic, payload))
    return keys


class Reduction:
    """State of one reduction: the sighting index, the counts and the rows held back."""

    def __init__(self, partial: Path, info: SnapshotInfo, shared_keys: set[str]) -> None:
        self.partial = partial
        self.info = info
        self.shared_keys = shared_keys
        self.index = SightingIndex()
        self.deliveries = BrokerHourCounts()
        self.copies = CopyCounts()
        self.dropped = DroppedCounts()
        self.minutes: dict[str, dict[int, tuple[float, float]]] = {}
        self.unreadable: Counter[str] = Counter()
        self.outside: Counter[str] = Counter()
        self.sources: dict[str, list[dict[str, Any]]] = {}
        self.held: list[dict[str, Any]] = []
        self.flushes: Counter[datetime] = Counter()

    def take(self, results: list[FileResult]) -> None:
        """Merge the files of one hour in received_at order and write its public-only rows."""
        for result in results:
            self._absorb_counts(result)
        written: dict[datetime, list[dict[str, Any]]] = {}
        for received_at, broker, row in heapq.merge(
            *(result.rows for result in results), key=lambda item: item[0]
        ):
            outcome = self.index.add(row["delivery_key"], Broker(broker))
            self.deliveries.add(received_at, Broker(broker), outcome)
            if outcome != "stored":
                continue
            full = {"snapshot_id": self.info.snapshot_id, **row}
            if row["delivery_key"] in self.shared_keys:
                self.held.append(full)
            else:
                full.update(seen_public=True, seen_meshstl=False)
                written.setdefault(hour_of(received_at), []).append(full)
        for hour, rows in written.items():
            self._flush(hour, rows)

    def _absorb_counts(self, result: FileResult) -> None:
        broker = result.broker.value
        self.deliveries.merge(result.counted)
        self.copies.merge(result.copies)
        self.dropped.merge(result.dropped)
        minutes = self.minutes.setdefault(broker, {})
        for minute, (low, high) in result.minutes.items():
            known = minutes.get(minute, (low, high))
            minutes[minute] = (min(known[0], low), max(known[1], high))
        self.unreadable[broker] += result.unreadable
        self.outside[broker] += result.outside
        self.sources.setdefault(broker, []).append(result.source)
        log.info("decode_batch", hour=result.hour, broker=broker, **dict(result.statuses))

    def _flush(self, hour: datetime, rows: list[dict[str, Any]]) -> None:
        self.flushes[hour] += 1
        path = flush_file(self.partial, DELIVERY_TABLE, hour, self.flushes[hour])
        size = write_rows(path, DELIVERY_TABLE, rows)
        log.info("flush", table=DELIVERY_TABLE, count=len(rows), bytes=size, path=path.name)

    def finish_rows(self) -> None:
        """Write the held rows with final flags, then merge every hour's flush files."""
        held: dict[datetime, list[dict[str, Any]]] = {}
        for row in self.held:
            row["seen_public"], row["seen_meshstl"] = self.index.flags(row["delivery_key"])
            held.setdefault(hour_of(row["received_at"]), []).append(row)
        for hour, rows in held.items():
            self._flush(hour, rows)
        for hour in sorted(self.flushes):
            merge_hour(self.partial, hour, self.flushes[hour])


def write_count_tables(reduction: Reduction) -> None:
    """One copy count file and one dropped count file per hour."""
    versions = {"decoder_version": DECODER_VERSION, "schema_version": SCHEMA_VERSION}
    snapshot = {"snapshot_id": reduction.info.snapshot_id}
    copies: dict[datetime, list[dict[str, Any]]] = {}
    for copy in reduction.copies.rows():
        copies.setdefault(copy.hour, []).append({**snapshot, **vars(copy), **versions})
    dropped: dict[datetime, list[dict[str, Any]]] = {}
    for drop in reduction.dropped.rows():
        dropped.setdefault(drop.hour, []).append({**snapshot, **vars(drop), **versions})
    for table, by_hour in (("mqtt_copy_count", copies), ("mqtt_dropped_count", dropped)):
        for hour, rows in sorted(by_hour.items()):
            write_rows(hour_file(reduction.partial, table, hour), table, rows)


def write_reduction_metadata(
    reduction: Reduction, sources: list[Source], hours: dict[str, float]
) -> Path:
    """README, content hashes and the manifest of a reduction."""
    windows = {
        source.broker.value: [
            source.start.isoformat() if source.start else None,
            source.end.isoformat() if source.end else None,
        ]
        for source in sources
    }
    run: dict[str, Any] = {
        "end_at": None,
        "max_raw_bytes": None,
        "stop_reason": "reduction_complete",
        "description": "A single capture from 2026-10-22 to 2026-10-25, reduced from the ADR "
        "0003 spike archive (public broker and MeshSTL broker).",
        "windows": windows,
        "unreadable_lines": {b.value: reduction.unreadable[b.value] for b in Broker},
        "outside_window": {b.value: reduction.outside[b.value] for b in Broker},
        "source_files": reduction.sources,
    }
    return write_metadata(
        reduction.partial, reduction.info, reduction.deliveries.rows(), hours, run
    )


def _tasks(sources: list[Source]) -> list[list[tuple[str, Source, Path]]]:
    by_hour: dict[str, list[tuple[str, Source, Path]]] = {}
    for source in sources:
        for hour, path in source.files().items():
            by_hour.setdefault(hour, []).append((hour, source, path))
    return [by_hour[hour] for hour in sorted(by_hour)]


def _results(groups: list[list[tuple[str, Source, Path]]], key: bytes, workers: int) -> Any:
    """FileResults grouped by hour, in hour order."""
    if workers <= 1:
        _set_worker_key(key)
        for group in groups:
            yield [decode_file(task) for task in group]
        return
    flat = [task for group in groups for task in group]
    sizes = [len(group) for group in groups]
    with ProcessPoolExecutor(workers, initializer=_set_worker_key, initargs=(key,)) as pool:
        stream = pool.map(decode_file, flat)
        for size in sizes:
            yield [next(stream) for _ in range(size)]


def reduce_capture(
    sources: list[Source], root: Path, info: SnapshotInfo, key: bytes, workers: int = 1
) -> Path:
    """Reduce capture folders into the snapshot `<root>/<info.snapshot_id>/`.

    Args:
        sources: The brokers' capture folders and windows.
        root: The snapshots folder.
        info: The snapshot's id, date, URLs, licence, brokers and topics.
        key: The HMAC key from keys.parse_hmac_key; never logged.
        workers: Worker processes; 1 decodes in this process.

    Returns:
        The final snapshot folder.

    Raises:
        SnapshotExistsError: if the snapshot exists.
        PartialExistsError: if `<id>.partial/` exists from an earlier run.
    """
    partial = start_snapshot(root, info.snapshot_id)
    reduction = Reduction(partial, info, meshstl_keys(sources, key))
    for results in _results(_tasks(sources), key, workers):
        reduction.take(results)
    reduction.finish_rows()
    write_count_tables(reduction)
    hours = write_coverage(partial, reduction.minutes)
    write_reduction_metadata(reduction, sources, hours)
    return finish(partial)


def main() -> None:
    """Reduce the 2026-10-22 archive into its snapshot; needs NODE_ID_HMAC_KEY."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--public", type=Path, default=PUBLIC_DIR)
    parser.add_argument("--meshstl", type=Path, default=MESHSTL_DIR)
    parser.add_argument("--workers", type=int, default=11)
    args = parser.parse_args()
    settings = get_ingester_settings()
    sources = [
        Source(Broker.PUBLIC, args.public),
        Source(Broker.MESHSTL, args.meshstl, *MESHSTL_WINDOW),
    ]
    folder = reduce_capture(
        sources, settings.snapshots_dir, DEFAULT_INFO, settings.hmac_key(), args.workers
    )
    log.info("reduction_done", snapshot_id=SNAPSHOT_ID, path=str(folder))


if __name__ == "__main__":
    main()
