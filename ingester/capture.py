"""One on-demand capture run: allowlist and pseudonyms in memory, flush files, merge at exit.

The MQTT client lives in `docs/adr/0003-spike/capture.py`, which feeds every
delivery to CaptureRun.receive and asks maybe_flush and stop_reason once a
second. This module holds no network code, so it runs on a fake clock in
tests. Each delivery is decoded at once by ingester.decoder; only allowlisted
fields and counts stay in memory, and nothing received at or after END_AT is
kept. A flush writes one file per table and hour; each file that fails is
retried once, and a second failure raises with the unwritten batches kept and
the written ones never written again, leaving the `.partial` folder without a
manifest. At exit the flush files are merged per
hour and the README and manifest record the parameters and the stop reason
(ADR 0005 items 1 and 6).
"""

import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from ingester.allowlist import DECODER_VERSION, SCHEMA_VERSION
from ingester.counts import BrokerHourCounts, CopyCounts, DroppedCounts, hour_of
from ingester.decoder import CopyDelivery, StoredDelivery, decode_delivery
from ingester.finalise import (
    COUNT_TABLES,
    DELIVERY_TABLE,
    SnapshotInfo,
    finish,
    merge_count_hour,
    merge_hour,
    write_coverage,
    write_metadata,
)
from ingester.flush import FlushPolicy, folder_bytes, stop_reason
from ingester.parquet import write_rows
from ingester.reduce import LICENCE
from ingester.sightings import Broker, SightingIndex
from ingester.snapshot import flush_file, start_snapshot

log = structlog.get_logger(component="ingester.capture")

# (table, hour, rows) of one flush file.
Batch = tuple[str, datetime, list[dict[str, Any]]]


@dataclass(frozen=True)
class RunParameters:
    """What one capture run is started with (ADR 0005 item 1)."""

    host: str
    port: int
    source: Broker
    topics: list[str]
    end_at: datetime
    max_raw_bytes: int
    snapshot_id: str
    licence: str = LICENCE
    meshstl_approval: str | None = None


def _utc_now() -> datetime:
    return datetime.now(UTC)


class CaptureRun:
    """The in-memory state of one run and its `.partial` folder."""

    def __init__(
        self,
        root: Path,
        params: RunParameters,
        key: bytes,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = _utc_now,
        interval_s: float = 300.0,
        max_records: int = 5000,
    ) -> None:
        self.params = params
        self.key = key
        self.now = now
        self.started_at = now()
        self.policy = FlushPolicy(interval_s, max_records, clock)
        self.partial = start_snapshot(root, params.snapshot_id)
        self.lock = threading.Lock()
        self.index = SightingIndex()
        self.deliveries = BrokerHourCounts()
        self.minutes: dict[int, tuple[float, float]] = {}
        self.flushes: Counter[tuple[str, datetime]] = Counter()
        # Batches taken from the buffer and not yet written; a failed flush leaves them here.
        self.unwritten: list[Batch] = []
        self.received = 0
        self._reset_buffers()

    def _reset_buffers(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.copies = CopyCounts()
        self.dropped = DroppedCounts()
        self.pending = 0

    def receive(self, topic: str, payload: bytes) -> None:
        """Decode one delivery into the buffer; payload and topic are dropped here."""
        received_at = self.now()
        if received_at >= self.params.end_at:
            return
        decoded = decode_delivery(topic, payload, received_at, self.key)
        with self.lock:
            self.received += 1
            self.pending += 1
            self._note_minute(received_at)
            if isinstance(decoded, StoredDelivery):
                self._keep_row(received_at, decoded.row)
                return
            self.deliveries.add(received_at, self.params.source, "counted")
            if isinstance(decoded, CopyDelivery):
                gateway, low = decoded.gateway_pseudonym, decoded.gateway_low_byte
                self.copies.add(received_at, decoded.topic, gateway, low)
            else:
                self.dropped.add(received_at, decoded.topic_class, decoded.reason)

    def _note_minute(self, received_at: datetime) -> None:
        at_s = received_at.timestamp()
        minute = int(at_s // 60)
        low, high = self.minutes.get(minute, (at_s, at_s))
        self.minutes[minute] = (min(low, at_s), max(high, at_s))

    def _keep_row(self, received_at: datetime, row: dict[str, Any]) -> None:
        outcome = self.index.add(row["delivery_key"], self.params.source)
        self.deliveries.add(received_at, self.params.source, outcome)
        if outcome == "stored":
            is_public = self.params.source is Broker.PUBLIC
            flags = {"seen_public": is_public, "seen_meshstl": not is_public}
            self.rows.append({"snapshot_id": self.params.snapshot_id, **row, **flags})

    def maybe_flush(self) -> None:
        """Flush when the interval has passed or the buffer is full."""
        if self.policy.due(self.pending):
            self.flush()

    def stop_reason(self) -> str | None:
        """end_at, max_raw_bytes or None, measured now."""
        used = folder_bytes(self.partial)
        return stop_reason(self.now(), self.params.end_at, used, self.params.max_raw_bytes)

    def flush(self) -> None:
        """Write the buffer as flush files, one per table and hour.

        Each file gets one retry. A batch is dropped from the buffer only once
        it is written, so a second failure raises with every unwritten batch
        kept and no written batch is ever written again.
        """
        with self.lock:
            self.unwritten += self._batches()
            self._reset_buffers()
            while self.unwritten:
                self._write(*self.unwritten[0])
                self.unwritten.pop(0)
            self.policy.flushed()

    def _batches(self) -> list[Batch]:
        snapshot = {"snapshot_id": self.params.snapshot_id}
        versions = {"decoder_version": DECODER_VERSION, "schema_version": SCHEMA_VERSION}
        by_key: dict[tuple[str, datetime], list[dict[str, Any]]] = {}
        for row in self.rows:
            by_key.setdefault((DELIVERY_TABLE, hour_of(row["received_at"])), []).append(row)
        for copy in self.copies.rows():
            row = {**snapshot, **vars(copy), **versions}
            by_key.setdefault(("mqtt_copy_count", copy.hour), []).append(row)
        for drop in self.dropped.rows():
            row = {**snapshot, **vars(drop), **versions}
            by_key.setdefault(("mqtt_dropped_count", drop.hour), []).append(row)
        return [(table, hour, rows) for (table, hour), rows in sorted(by_key.items())]

    def _write(self, table: str, hour: datetime, rows: list[dict[str, Any]]) -> None:
        """Write one flush file, retrying once at the same path."""
        number = self.flushes[(table, hour)] + 1
        path = flush_file(self.partial, table, hour, number)
        started = time.monotonic()
        try:
            size = write_rows(path, table, rows)
        except OSError as first:
            # Safe to retry: the same path is rewritten whole, and a second failure raises.
            log.warning(
                "flush_retry",
                table=table,
                hour=f"{hour:%Y-%m-%dT%H}",
                path=path.name,
                error=first.__class__.__name__,
            )
            size = write_rows(path, table, rows)
        self.flushes[(table, hour)] = number
        duration_s = round(time.monotonic() - started, 3)
        log.info(
            "flush", table=table, count=len(rows), bytes=size, path=path.name, duration_s=duration_s
        )

    def finish(self, reason: str) -> Path:
        """Flush, merge every hour, write coverage, README and manifest, and rename."""
        self.flush()
        for (table, hour), flushes in sorted(self.flushes.items()):
            if table == DELIVERY_TABLE:
                merge_hour(self.partial, hour, flushes)
            elif table in COUNT_TABLES:
                merge_count_hour(self.partial, table, hour, flushes)
        source = self.params.source.value
        hours = write_coverage(self.partial, {source: self.minutes})
        run = {
            "end_at": self.params.end_at.isoformat(),
            "max_raw_bytes": self.params.max_raw_bytes,
            "stop_reason": reason,
            "description": "One capture run, started by hand, from one broker.",
            "windows": {source: [None, self.params.end_at.isoformat()]},
            "meshstl_approval": self.params.meshstl_approval,
        }
        write_metadata(self.partial, self._info(), self.deliveries.rows(), hours, run)
        folder = finish(self.partial)
        log.info("stop", reason=reason, received=self.received, path=str(folder))
        return folder

    def _info(self) -> SnapshotInfo:
        return SnapshotInfo(
            snapshot_id=self.params.snapshot_id,
            capture_date=f"{self.started_at:%Y-%m-%d}",
            source_url=[f"mqtt://{self.params.host}:{self.params.port}"],
            licence=self.params.licence,
            brokers=[self.params.host],
            topics={self.params.source.value: list(self.params.topics)},
        )
