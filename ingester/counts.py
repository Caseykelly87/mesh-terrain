"""Counts of deliveries that are not stored as rows (ADR 0005 item 5, ing_03).

Header-only internet copies are counted per (hour, topic, gateway pseudonym);
json, stat, unparsed and other deliveries per (hour, topic class, reason). The
snapshot id is added when a count table is written, since one run or reduction
fills one snapshot. Counts are summed across flushes with merge.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

# Each counted reason and the topic classes it may come from.
REASON_CLASSES: dict[str, frozenset[str]] = {
    "json": frozenset({"json"}),
    "stat": frozenset({"stat"}),
    "unparsed": frozenset({"e", "map"}),
    "other": frozenset({"other"}),
}


def hour_of(received_at: datetime) -> datetime:
    """The UTC hour a delivery belongs to.

    Args:
        received_at: Our clock's time of receipt, timezone-aware.

    Raises:
        ValueError: if received_at has no timezone.
    """
    if received_at.tzinfo is None:
        raise ValueError("received_at must be timezone-aware")
    return received_at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


@dataclass(frozen=True, order=True)
class CopyCountRow:
    """Internet copies of one hour, topic and gateway."""

    hour: datetime
    topic: str
    gateway_pseudonym: str
    gateway_low_byte: int
    copy_count: int


@dataclass(frozen=True, order=True)
class DroppedCountRow:
    """Deliveries of one hour, topic class and reason that were not stored."""

    hour: datetime
    topic_class: str
    reason: str
    delivery_count: int


class CopyCounts:
    """Internet copies counted per (hour, landed topic, gateway pseudonym)."""

    def __init__(self) -> None:
        self._counts: Counter[tuple[datetime, str, str, int]] = Counter()

    def add(
        self, received_at: datetime, topic: str, gateway_pseudonym: str, gateway_low_byte: int
    ) -> None:
        """Count one copy under its landed topic and the gateway pseudonym and low id byte."""
        self._counts[(hour_of(received_at), topic, gateway_pseudonym, gateway_low_byte)] += 1

    def merge(self, other: "CopyCounts") -> None:
        """Add another counter's counts to this one."""
        self._counts.update(other._counts)

    def total(self) -> int:
        """Copies counted."""
        return sum(self._counts.values())

    def rows(self) -> list[CopyCountRow]:
        """One row per key, sorted by hour, topic and gateway."""
        return sorted(CopyCountRow(*key, count) for key, count in self._counts.items())


class DroppedCounts:
    """Deliveries not stored as rows, counted per (hour, topic class, reason)."""

    def __init__(self) -> None:
        self._counts: Counter[tuple[datetime, str, str]] = Counter()

    def add(self, received_at: datetime, topic_class: str, reason: str) -> None:
        """Count one delivery.

        Raises:
            ValueError: if reason is not json, stat, unparsed or other, or does
                not apply to topic_class.
        """
        if topic_class not in REASON_CLASSES.get(reason, frozenset()):
            raise ValueError(f"reason {reason!r} does not apply to topic class {topic_class!r}")
        self._counts[(hour_of(received_at), topic_class, reason)] += 1

    def merge(self, other: "DroppedCounts") -> None:
        """Add another counter's counts to this one."""
        self._counts.update(other._counts)

    def total(self) -> int:
        """Deliveries counted."""
        return sum(self._counts.values())

    def rows(self) -> list[DroppedCountRow]:
        """One row per key, sorted by hour, topic class and reason."""
        return sorted(DroppedCountRow(*key, count) for key, count in self._counts.items())
