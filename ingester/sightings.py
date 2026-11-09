"""Which brokers delivered each stored delivery (ADR 0005 item 4, ing_01).

The reduction reads both brokers' captures into one snapshot. A delivery with
the same bytes on both brokers has one delivery key, so it becomes one row
with seen_public and seen_meshstl both true; the flags are set here, before
any load, so the loader's insert-ignore never drops a sighting. The earliest
sighting fixes received_at and therefore the hour file the row lands in.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Broker(StrEnum):
    """The two brokers of the 2026-10-22 capture."""

    PUBLIC = "public"  # mqtt.meshtastic.org
    MESHSTL = "meshstl"  # mqtt.meshstl.org


@dataclass
class Sighting:
    """Broker flags and earliest receipt time of one delivery key."""

    received_at: datetime
    seen_public: bool = False
    seen_meshstl: bool = False

    def mark(self, broker: Broker, received_at: datetime) -> None:
        """Set the broker's flag and keep the earlier receipt time."""
        if broker is Broker.PUBLIC:
            self.seen_public = True
        else:
            self.seen_meshstl = True
        self.received_at = min(self.received_at, received_at)


class SightingIndex:
    """Sightings by delivery key."""

    def __init__(self) -> None:
        self._sightings: dict[str, Sighting] = {}

    def add(self, key: str, broker: Broker, received_at: datetime) -> bool:
        """Record one delivery; returns True when the key was not seen before."""
        sighting = self._sightings.get(key)
        is_new = sighting is None
        if sighting is None:
            sighting = self._sightings[key] = Sighting(received_at)
        sighting.mark(broker, received_at)
        return is_new

    def get(self, key: str) -> Sighting:
        """The sighting of a key.

        Raises:
            KeyError: if the key was never added.
        """
        return self._sightings[key]

    def __len__(self) -> int:
        return len(self._sightings)
