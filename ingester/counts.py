"""Counts of deliveries that are not stored as rows, and of every delivery per hour and broker.

Header-only internet copies are counted per (hour, topic, gateway pseudonym);
json, stat, unparsed and other deliveries per (hour, topic class, reason)
(ADR 0005 item 5). Every delivery received is also counted per (hour, broker),
on its own and by what happened to it: stored, counted, merged_cross_broker or
merged_repeat, so the ing_03 balance can be checked from the manifest. The
portnum rule tally counts the decrypt_failed deliveries and rows that the
decoder failed by their decrypted portnum (ing_03, 2026-11-13). The snapshot id is
added when a table is written, since one run or reduction fills one snapshot.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

from ingester.sightings import Broker

# Each counted reason and the topic classes it may come from.
REASON_CLASSES: dict[str, frozenset[str]] = {
    "json": frozenset({"json"}),
    "stat": frozenset({"stat"}),
    "unparsed": frozenset({"e", "map"}),
    "other": frozenset({"other"}),
}
OUTCOMES = ("stored", "counted", "merged_cross_broker", "merged_repeat")
MANIFEST_FIELDS = ("received", *OUTCOMES, "rows_with_flag")

CopyKey = tuple[datetime, str, str]
DroppedKey = tuple[datetime, str, str]


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
    """Internet copies counted per (hour, landed topic, gateway pseudonym), the contract key."""

    def __init__(self) -> None:
        self._counts: Counter[CopyKey] = Counter()
        self._low_bytes: dict[str, int] = {}

    def _note_low_byte(self, gateway_pseudonym: str, gateway_low_byte: int) -> None:
        known = self._low_bytes.setdefault(gateway_pseudonym, gateway_low_byte)
        if known != gateway_low_byte:
            raise ValueError("one gateway pseudonym arrived with two low bytes")

    def add(
        self, received_at: datetime, topic: str, gateway_pseudonym: str, gateway_low_byte: int
    ) -> None:
        """Count one copy under its landed topic and gateway pseudonym.

        Raises:
            ValueError: if the pseudonym was seen before with another low byte.
        """
        self._note_low_byte(gateway_pseudonym, gateway_low_byte)
        self._counts[(hour_of(received_at), topic, gateway_pseudonym)] += 1

    def merge(self, other: "CopyCounts") -> None:
        """Add another counter's counts to this one."""
        for pseudonym, low in other._low_bytes.items():
            self._note_low_byte(pseudonym, low)
        self._counts.update(other._counts)

    def total(self) -> int:
        """Copies counted."""
        return sum(self._counts.values())

    def by_key(self) -> dict[CopyKey, int]:
        """Counts by (hour, topic, gateway pseudonym)."""
        return dict(self._counts)

    def rows(self) -> list[CopyCountRow]:
        """One row per key, sorted by hour, topic and gateway."""
        return sorted(
            CopyCountRow(hour, topic, gateway, self._low_bytes[gateway], count)
            for (hour, topic, gateway), count in self._counts.items()
        )


class DroppedCounts:
    """Deliveries not stored as rows, counted per (hour, topic class, reason)."""

    def __init__(self) -> None:
        self._counts: Counter[DroppedKey] = Counter()

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

    def by_key(self) -> dict[DroppedKey, int]:
        """Counts by (hour, topic class, reason)."""
        return dict(self._counts)

    def rows(self) -> list[DroppedCountRow]:
        """One row per key, sorted by hour, topic class and reason."""
        return sorted(DroppedCountRow(*key, count) for key, count in self._counts.items())


class BrokerHourCounts:
    """Every delivery received, per (hour, broker), by what became of it (ing_03).

    Received is counted on its own as each delivery is read, so the manifest's
    balance (received equals stored plus counted plus both merged counts) is a
    check, not an identity. rows_with_flag counts the stored rows whose first
    sighting falls in the hour and that carry the broker's flag, which is what
    the loader can compare with raw per hour and broker.
    """

    def __init__(self) -> None:
        self._counts: Counter[tuple[datetime, str, str]] = Counter()

    def receive(self, received_at: datetime, broker: Broker) -> None:
        """Count one delivery as it is read, before anything is decided about it."""
        self._counts[(hour_of(received_at), str(broker), "received")] += 1

    def add(self, received_at: datetime, broker: Broker, outcome: str, count: int = 1) -> None:
        """Count deliveries of one outcome.

        Raises:
            ValueError: if outcome is not stored, counted, merged_cross_broker or merged_repeat.
        """
        if outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome {outcome!r}")
        self._counts[(hour_of(received_at), str(broker), outcome)] += count

    def add_row(self, received_at: datetime, seen_public: bool, seen_meshstl: bool) -> None:
        """Count one stored row, as written, under each broker whose flag it carries."""
        hour = hour_of(received_at)
        for broker, seen in ((Broker.PUBLIC, seen_public), (Broker.MESHSTL, seen_meshstl)):
            if seen:
                self._counts[(hour, str(broker), "rows_with_flag")] += 1

    def merge(self, other: "BrokerHourCounts") -> None:
        """Add another counter's counts to this one."""
        self._counts.update(other._counts)

    def rows(self) -> list[dict[str, str | int]]:
        """One manifest row per (hour, broker) with every field of MANIFEST_FIELDS."""
        keys = sorted({(hour, broker) for hour, broker, _ in self._counts})
        rows: list[dict[str, str | int]] = []
        for hour, broker in keys:
            row: dict[str, str | int] = {"hour": f"{hour:%Y-%m-%dT%H}", "broker": broker}
            row.update({name: self._counts[(hour, broker, name)] for name in MANIFEST_FIELDS})
            rows.append(row)
        return rows


class PortnumRuleTally:
    """decrypt_failed deliveries and stored rows that the decrypted portnum rule produced."""

    def __init__(self) -> None:
        self.deliveries: Counter[str] = Counter()
        self.rows: Counter[str] = Counter()

    def add_delivery(self, broker: Broker) -> None:
        """Count one delivery the rule failed, as it is decoded."""
        self.deliveries[str(broker)] += 1

    def add_row(self, seen_public: bool, seen_meshstl: bool) -> None:
        """Count one stored row the rule failed, under each broker flag it carries."""
        self.rows["seen_public"] += int(seen_public)
        self.rows["seen_meshstl"] += int(seen_meshstl)

    def merge(self, other: "PortnumRuleTally") -> None:
        """Add another tally's counts to this one."""
        self.deliveries.update(other.deliveries)
        self.rows.update(other.rows)

    def manifest(self, brokers: list[Broker]) -> dict[str, dict[str, int]]:
        """The manifest entry: deliveries per broker and rows per broker flag."""
        return {
            "deliveries": {str(broker): self.deliveries[str(broker)] for broker in brokers},
            "rows": {f"seen_{broker}": self.rows[f"seen_{broker}"] for broker in brokers},
        }
