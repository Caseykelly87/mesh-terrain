"""Which brokers delivered each stored delivery, and which deliveries were merged (ing_01, ing_03).

The reduction reads both brokers' captures into one snapshot, in received_at
order. A delivery with the same bytes on both brokers has one delivery key, so
it becomes one row with seen_public and seen_meshstl both true; the flags are
set here, before any load, so the loader's insert-ignore never drops a
sighting. The first sighting of a key is stored; every later one is merged into
that row and counted as merged_cross_broker when its broker had not yet sent
the key, merged_repeat when it had. A rise in merged_repeat is what a key that
merges distinct deliveries would look like.

Keys are held as 32 raw bytes and flags as a two-bit integer, so the index for
the 2026-10-22 capture (about 4.3 million stored deliveries) stays near 0.5 GB.
"""

from enum import StrEnum
from typing import Literal

Outcome = Literal["stored", "merged_cross_broker", "merged_repeat"]


class Broker(StrEnum):
    """The two brokers of the 2026-10-22 capture."""

    PUBLIC = "public"  # mqtt.meshtastic.org
    MESHSTL = "meshstl"  # mqtt.meshstl.org


_BIT = {Broker.PUBLIC: 1, Broker.MESHSTL: 2}


def _raw(key: str) -> bytes:
    raw = bytes.fromhex(key)
    if len(raw) != 32:
        raise ValueError("a delivery key is 64 hex characters")
    return raw


class SightingIndex:
    """Broker flags by delivery key."""

    def __init__(self) -> None:
        self._flags: dict[bytes, int] = {}

    def add(self, key: str, broker: Broker) -> Outcome:
        """Record one delivery and say whether it is stored or merged.

        Raises:
            ValueError: if key is not a 64-character hex delivery key.
        """
        raw = _raw(key)
        bit = _BIT[broker]
        flags = self._flags.get(raw)
        if flags is None:
            self._flags[raw] = bit
            return "stored"
        self._flags[raw] = flags | bit
        return "merged_repeat" if flags & bit else "merged_cross_broker"

    def flags(self, key: str) -> tuple[bool, bool]:
        """(seen_public, seen_meshstl) of a key.

        Raises:
            KeyError: if the key was never added.
        """
        flags = self._flags[_raw(key)]
        return bool(flags & _BIT[Broker.PUBLIC]), bool(flags & _BIT[Broker.MESHSTL])

    def __len__(self) -> int:
        return len(self._flags)
