"""The pairs of a capture snapshot under the contract's link-hour rules, each with the box it used.

A pair is a (relayer, gateway) with at least one kept link-hour under
`ingester.link_rules`, which applies `lnk_01` to `lnk_06` on the merged
snapshot, both brokers together (Casey, issue #55, Q1: "item 4 implements the
contract as written on the merged snapshot; the spike's per-run figures stay
the record, and the pair count is whatever the contract gives"). The spike
scripts under `docs/adr/0003-spike/` are no longer run; they stay as the
historical record (Casey, PR #52, 4).

Each pair carries the box its counted receipts used at each end, over its
kept link-hours (Casey, issue #44, 2026-11-17: "gives each pair the box its
counted receipts used, so a moving node is placed per pair, and a pair whose
receipts used more than one box is dropped and reported to me"). A kept hour
has one box per end already (`lnk_02`, step 8); a pair whose kept hours used
two different boxes at an end, all four edges compared, is listed in
`moved_pairs` with those boxes, and `ingester.tile_selection` drops it after
the 2026-11-08 counts check, as it drops a straddling row (Casey, issue #55
comment 6985802330, item 3). The pair count `--expect-pairs` checks is the
contract's, kept and dropped together.

Only receipts in kept link-hours set a pair's box (Casey, the same comment,
item 2, (b): "only the receipts the validation uses must agree on
position"). A kept pair whose hours not kept show another box at an end, on
their counted receipts, is listed in `other_box_pairs`, so the command can
report it to Casey locally ("kept but reported to me in the session, never
on issue #44"). Worked example: C to G3 kept at 11:00, 12:00 and 13:00 on
G3's own box, and heard twice at 14:00 while G3 read a box 2 km west, is kept
at the own box and listed; under reading (a), every counted receipt, the pair
would have two boxes and be dropped.

A node's boxes are the boxes the remaining pairs used for it.
`ingester.tile_selection` places a node once per pair, at the box that pair
used, a merged rectangle at the midpoint of its edges, with one layer row per
distinct box; a node whose boxes lie on both sides of the tile edge
straddles it. Its precise flag is true when any of its pairs' kept hours flagged
it (`pos_04`; Casey, D7), so a node is never treated as less precise than one
of its hours.

What a position is follows the contract (`pos_01`, `pos_03`): the count of
rows with one coordinate missing is reported. Every file read is listed in the
snapshot's manifest and matches its sha256 (`snp_01`). The snapshot must be
the single merged one, its manifest listing both brokers (`require_merged`),
since the rules group a broadcast's copies within the rows they read and
`pos_04`'s MeshSTL flag reads every copy in any snapshot (Casey, issue #55
comment 7016583529, item 4). Nothing here prints or logs a coordinate.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import structlog

from ingester.link_cells import LinkBox
from ingester.link_rules import KEPT, LinkHour, LinkRulesResult, apply_link_rules
from ingester.msa import InArea, msa_contains
from pipeline.common.snapshot import SnapshotError, verified_manifest

log = structlog.get_logger(component="ingester.link_pairs")

DELIVERY_FOLDER = "mqtt_message/"
# The brokers a merged capture snapshot's manifest lists (`ingester.reduce`).
MERGED_BROKERS = frozenset({"mqtt.meshtastic.org", "mqtt.meshstl.org"})
COLUMNS = [
    "delivery_key", "received_at", "gateway_pseudonym", "gateway_low_byte", "origin_pseudonym",
    "origin_low_byte", "packet_id", "relay_node", "via_mqtt", "rx_snr", "rx_rssi", "portnum",
    "position_source", "latitude_i", "longitude_i", "precision_bits", "neighbour_pseudonyms",
    "neighbour_low_bytes", "seen_public", "seen_meshstl",
]  # fmt: skip
USABLE_BITS = 15
ENDS = ("relayer", "gateway")


class SelectionError(Exception):
    """Engine inputs are refused: the snapshot or output folder does not allow them."""


@dataclass(frozen=True)
class NodePositions:
    """The boxes a node's pairs' counted receipts used, and whether it is a precise source.

    Attributes:
        boxes: At least one box (`lnk_02`), each a cell or a merged rectangle.
        precise_source: Whether one of its pairs' kept hours flagged it (`pos_04`).

    Raises:
        ValueError: if boxes is empty.
    """

    boxes: frozenset[LinkBox]
    precise_source: bool

    def __post_init__(self) -> None:
        if not self.boxes:
            raise ValueError("a node needs at least one box")

    @property
    def one_box(self) -> LinkBox | None:
        """The node's box when every pair used the same one, all four edges equal; else None."""
        edges = {box.edges for box in self.boxes}
        return next(iter(self.boxes)) if len(edges) == 1 else None


@dataclass(frozen=True)
class PairActivity:
    """One pair with a kept link-hour, the measures its busyness can be ranked by, and its boxes.

    Attributes:
        relayer: The relayer's pseudonym.
        gateway: The gateway's pseudonym.
        link_hours: Kept link-hours.
        link_hours_public: Kept link-hours with a counted receipt the public broker carried.
        link_hours_meshstl: Kept link-hours with a counted receipt MeshSTL carried.
        receipts: Counted receipts in the kept link-hours.
        relayer_box: The box the counted receipts used for the relayer.
        gateway_box: The box they used for the gateway.
        relayer_precise_source: Whether a kept hour flagged the relayer (`pos_04`).
        gateway_precise_source: Whether a kept hour flagged the gateway.
    """

    relayer: str
    gateway: str
    link_hours: int
    link_hours_public: int
    link_hours_meshstl: int
    receipts: int
    relayer_box: LinkBox | None = None
    gateway_box: LinkBox | None = None
    relayer_precise_source: bool = False
    gateway_precise_source: bool = False


@dataclass(frozen=True)
class MovedPair:
    """A pair to drop because an end used more than one box across its kept hours.

    Attributes:
        relayer: The relayer's pseudonym.
        gateway: The gateway's pseudonym.
        ends: `relayer`, `gateway` or both, relayer first.
        link_hours: Its kept link-hours.
        relayer_boxes: The boxes its kept hours used for the relayer.
        gateway_boxes: The boxes they used for the gateway.
    """

    relayer: str
    gateway: str
    ends: tuple[str, ...]
    link_hours: int
    relayer_boxes: frozenset[LinkBox] = frozenset()
    gateway_boxes: frozenset[LinkBox] = frozenset()


@dataclass(frozen=True)
class OtherBoxPair:
    """A kept pair whose hours not kept show another box at an end (Casey, item 2, (b)).

    Attributes:
        relayer: The relayer's pseudonym.
        gateway: The gateway's pseudonym.
        ends: `relayer`, `gateway` or both, relayer first.
    """

    relayer: str
    gateway: str
    ends: tuple[str, ...]


@dataclass(frozen=True)
class CaptureLinks:
    """The pairs of a capture with one box per end, the pairs dropped, and every node's boxes.

    Attributes:
        pairs: The pairs with one box per end, sorted by relayer then gateway.
        nodes: Per node in those pairs, its boxes.
        rows: Delivery rows read.
        one_coordinate_missing: Position rows with exactly one coordinate null.
        moved_pairs: The pairs to drop for an end with more than one box.
        other_box_pairs: The kept pairs whose hours not kept show another box.
        direct_hop_contradicted: Receipts `lnk_01` dropped under Q4 (a).
        one_box_dropped_hours: Link-hours dropped for more than one box (step 8).
    """

    pairs: tuple[PairActivity, ...]
    nodes: dict[str, NodePositions]
    rows: int
    one_coordinate_missing: int
    moved_pairs: tuple[MovedPair, ...] = ()
    other_box_pairs: tuple[OtherBoxPair, ...] = ()
    direct_hop_contradicted: int = 0
    one_box_dropped_hours: int = 0

    @property
    def pair_count(self) -> int:
        """The contract's pair count: every pair with a kept link-hour."""
        return len(self.pairs) + len(self.moved_pairs)

    @property
    def pair_counts(self) -> dict[str, int]:
        return {
            "pairs": self.pair_count,
            "placed": len(self.pairs),
            "more than one box": len(self.moved_pairs),
        }


def read_capture_rows(folder: Path) -> list[dict[str, Any]]:
    """The delivery rows of a finished capture snapshot, after checking it against its manifest.

    Args:
        folder: The finished snapshot folder.

    Returns:
        One dict per stored delivery, with the columns the link rules read.

    Raises:
        SnapshotError: if the folder has no manifest, a file differs from it,
            or the manifest does not list both brokers.
    """
    started = time.monotonic()
    manifest = verified_manifest(folder)
    require_merged(manifest)
    paths = sorted(
        entry["path"]
        for entry in manifest["files"]
        if entry["path"].startswith(DELIVERY_FOLDER) and entry["path"].endswith(".parquet")
    )
    rows: list[dict[str, Any]] = []
    for path in paths:
        rows += pq.read_table(folder / path, columns=COLUMNS).to_pylist()
    duration_s = round(time.monotonic() - started, 3)
    log.info(
        "capture_read", path=str(folder), files=len(paths), rows=len(rows), duration_s=duration_s
    )
    return rows


def require_merged(manifest: dict[str, Any]) -> None:
    """Refuse a capture snapshot that is not the single merged one.

    `pos_04`'s MeshSTL flag reads every copy of a delivery in any snapshot
    (Casey, issue #55 comment 6985802330, item 7), and the link rules read
    one snapshot, so a broadcast's copies must all be in it: the snapshot
    must merge both brokers (Casey, issue #55 comment 7016583529, item 4).

    Raises:
        SnapshotError: if the manifest's `brokers` does not list both brokers.
    """
    listed = set(manifest.get("brokers") or [])
    if not MERGED_BROKERS <= listed:
        missing = ", ".join(sorted(MERGED_BROKERS - listed))
        raise SnapshotError(f"not a merged snapshot of both brokers; its manifest lacks {missing}")


def capture_links(rows: Sequence[dict[str, Any]], inside: InArea | None = None) -> CaptureLinks:
    """The pairs of a capture under the contract's link-hour rules, and their nodes' boxes.

    Args:
        rows: Delivery rows from read_capture_rows, both brokers.
        inside: The MSA test; `msa_contains()` when None.

    Returns:
        The pairs with one box per end in a stable order, the pairs dropped
        for more than one box, every placed node's boxes and the counts.
    """
    started = time.monotonic()
    result = apply_link_rules(rows, inside or msa_contains())
    flags = {row["delivery_key"]: (row["seen_public"], row["seen_meshstl"]) for row in rows}
    pairs, moved = _pairs(result, flags)
    missing = sum(
        1
        for row in rows
        if row["position_source"] is not None
        and (row["latitude_i"] is None) != (row["longitude_i"] is None)
    )
    links = CaptureLinks(
        pairs, _nodes(pairs), len(rows), missing, moved, _other_box_pairs(result, pairs),
        result.direct_hop_contradicted, result.one_box_dropped_hours,
    )  # fmt: skip
    duration_s = round(time.monotonic() - started, 3)
    log.info("pairs_found", **links.pair_counts, duration_s=duration_s)
    return links


@dataclass
class _Tally:
    """A pair's kept hours, as they are gathered."""

    hours: list[LinkHour] = field(default_factory=list)
    public: set[datetime] = field(default_factory=set)
    meshstl: set[datetime] = field(default_factory=set)


def _pairs(
    result: LinkRulesResult, flags: dict[str, tuple[bool, bool]]
) -> tuple[tuple[PairActivity, ...], tuple[MovedPair, ...]]:
    """The pairs with one box per end over their kept hours, and the pairs dropped.

    Args:
        result: The link rules' result.
        flags: Per delivery key, whether the public broker and MeshSTL carried it.
    """
    tallies: dict[tuple[str, str], _Tally] = {}
    for hour in result.link_hours:
        if hour.outcome != KEPT:
            continue
        tally = tallies.setdefault((hour.relayer, hour.gateway), _Tally())
        tally.hours.append(hour)
        for key in hour.receipts:
            public, meshstl = flags[key]
            if public:
                tally.public.add(hour.hour)
            if meshstl:
                tally.meshstl.add(hour.hour)
    pairs, moved = [], []
    for (relayer, gateway), tally in sorted(tallies.items()):
        ends = _ends_with_two_boxes(tally.hours)
        if ends:
            moved.append(_moved(relayer, gateway, ends, tally.hours))
        else:
            pairs.append(_activity(relayer, gateway, tally))
    return tuple(pairs), tuple(moved)


def _moved(
    relayer: str, gateway: str, ends: tuple[str, ...], hours: Sequence[LinkHour]
) -> MovedPair:
    return MovedPair(
        relayer, gateway, ends, len(hours),
        frozenset(hour.relayer_box for hour in hours if hour.relayer_box is not None),
        frozenset(hour.gateway_box for hour in hours if hour.gateway_box is not None),
    )  # fmt: skip


def _other_box_pairs(
    result: LinkRulesResult, pairs: Sequence[PairActivity]
) -> tuple[OtherBoxPair, ...]:
    """The kept pairs whose hours not kept used another box on a counted receipt."""
    kept = {(pair.relayer, pair.gateway): pair for pair in pairs}
    found: dict[tuple[str, str], set[str]] = {}
    for hour in result.link_hours:
        pair = kept.get((hour.relayer, hour.gateway))
        if pair is None or hour.outcome == KEPT:
            continue
        for key in hour.receipts:
            receipt = result.receipts[key]
            for end, used, own in (
                ("relayer", receipt.relayer_box, pair.relayer_box),
                ("gateway", receipt.gateway_box, pair.gateway_box),
            ):
                if used is not None and own is not None and used.edges != own.edges:
                    found.setdefault((hour.relayer, hour.gateway), set()).add(end)
    return tuple(
        OtherBoxPair(relayer, gateway, tuple(end for end in ENDS if end in ends))
        for (relayer, gateway), ends in sorted(found.items())
    )


def _ends_with_two_boxes(hours: Sequence[LinkHour]) -> tuple[str, ...]:
    relayer = {hour.relayer_box.edges for hour in hours if hour.relayer_box is not None}
    gateway = {hour.gateway_box.edges for hour in hours if hour.gateway_box is not None}
    return tuple(end for end, boxes in zip(ENDS, (relayer, gateway), strict=True) if len(boxes) > 1)


def _activity(relayer: str, gateway: str, tally: _Tally) -> PairActivity:
    first = tally.hours[0]
    return PairActivity(
        relayer, gateway, len(tally.hours), len(tally.public), len(tally.meshstl),
        sum(hour.packets for hour in tally.hours), first.relayer_box, first.gateway_box,
        any(bool(hour.relayer_precise_source) for hour in tally.hours),
        any(bool(hour.gateway_precise_source) for hour in tally.hours),
    )  # fmt: skip


def _nodes(pairs: Sequence[PairActivity]) -> dict[str, NodePositions]:
    """Per node, the boxes its pairs used and whether any of them flagged it precise."""
    boxes: dict[str, set[LinkBox]] = {}
    precise: dict[str, bool] = {}
    for pair in pairs:
        ends = (
            (pair.relayer, pair.relayer_box, pair.relayer_precise_source),
            (pair.gateway, pair.gateway_box, pair.gateway_precise_source),
        )
        for node, box, flag in ends:
            if box is not None:
                boxes.setdefault(node, set()).add(box)
                precise[node] = precise.get(node, False) or flag
    return {node: NodePositions(frozenset(found), precise[node]) for node, found in boxes.items()}
