"""When a capture run flushes and stops, and the checks on its merge at exit (ADR 0005 items 1, 6).

A run buffers decoded rows in memory and writes a flush file per table and hour
when the flush interval has passed or the buffer reaches its record cap. It
stops at END_AT or when the snapshot folder reaches MAX_RAW_BYTES, whichever
comes first, and records which one in the manifest. At exit each hour's flush
files are merged into one file; a flush file is removed only after the merged
file passes the check here. The clock is injected so tests run on a fake one.
"""

from collections import Counter
from collections.abc import Callable, Hashable, Iterable, Mapping
from datetime import datetime
from pathlib import Path


class MergeError(Exception):
    """A merged hour file does not hold what its flush files held."""


class FlushPolicy:
    """Decides when the buffer is written, from elapsed time and buffered records."""

    def __init__(self, interval_s: float, max_records: int, clock: Callable[[], float]) -> None:
        """Start the interval at the clock's current reading.

        Args:
            interval_s: Seconds between flushes.
            max_records: Buffered records that force a flush before the interval.
            clock: Monotonic seconds, time.monotonic in a run.

        Raises:
            ValueError: if either limit is not positive.
        """
        if interval_s <= 0 or max_records <= 0:
            raise ValueError("flush interval and record cap must be positive")
        self.interval_s = interval_s
        self.max_records = max_records
        self._clock = clock
        self._last_flush_s = clock()

    def due(self, pending: int) -> bool:
        """True when there is something to write and a limit is reached."""
        if pending <= 0:
            return False
        elapsed_s = self._clock() - self._last_flush_s
        return pending >= self.max_records or elapsed_s >= self.interval_s

    def flushed(self) -> None:
        """Restart the interval after a write."""
        self._last_flush_s = self._clock()


def parse_end_at(value: str) -> datetime:
    """Parse END_AT, ISO 8601 with an offset.

    Raises:
        ValueError: if the value does not parse or has no offset.
    """
    end_at = datetime.fromisoformat(value)
    if end_at.tzinfo is None:
        raise ValueError("END_AT needs an offset, for example 2026-10-25T08:00:00+00:00")
    return end_at


def folder_bytes(folder: Path) -> int:
    """Bytes of every file below folder, the measure MAX_RAW_BYTES caps."""
    return sum(path.stat().st_size for path in folder.rglob("*") if path.is_file())


def stop_reason(now: datetime, end_at: datetime, used_bytes: int, max_raw_bytes: int) -> str | None:
    """The reason a run stops now, or None to keep going.

    Args:
        now: The current time, timezone-aware.
        end_at: END_AT, timezone-aware.
        used_bytes: Bytes in the snapshot folder after the last flush.
        max_raw_bytes: MAX_RAW_BYTES.

    Returns:
        "end_at", "max_raw_bytes" or None; END_AT wins when both apply.
    """
    if now >= end_at:
        return "end_at"
    if used_bytes >= max_raw_bytes:
        return "max_raw_bytes"
    return None


def check_merged_keys(flush_keys: Iterable[Iterable[str]], merged_keys: Iterable[str]) -> None:
    """The merged delivery file holds each flushed delivery key exactly once.

    Raises:
        MergeError: if a key is missing, added or repeated in the merged file.
    """
    expected = {key for keys in flush_keys for key in keys}
    merged = Counter(merged_keys)
    repeated = sum(1 for count in merged.values() if count > 1)
    if repeated or set(merged) != expected:
        raise MergeError(
            f"merged keys differ: {len(expected - set(merged))} missing, "
            f"{len(set(merged) - expected)} added, {repeated} repeated"
        )


def check_merged_counts[K: Hashable](
    flush_counts: Iterable[Mapping[K, int]], merged_counts: Mapping[K, int]
) -> None:
    """The merged count file holds, per key, the sum of its flush files' counts.

    Args:
        flush_counts: Counts by key from each flush file.
        merged_counts: Counts by key from the merged file.

    Raises:
        MergeError: if any key's count differs, or a key is missing or added.
    """
    expected: Counter[K] = Counter()
    for counts in flush_counts:
        expected.update(counts)
    merged = Counter(merged_counts)
    differing = [key for key in expected.keys() | merged.keys() if expected[key] != merged[key]]
    if differing or set(merged) != set(expected):
        raise MergeError(f"merged counts differ from the flush files on {len(differing)} keys")
