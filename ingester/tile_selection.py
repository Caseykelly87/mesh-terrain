"""Which real nodes an engine run on one USGS 1 m tile uses, and whether each fits the DEM.

The sites are chosen by tile membership (Casey, issue #44, busiest pair,
2026-11-17): the in-tile gateway of every pair that touches the tile, as the
2026-11-08 counts define touching (docs/measurements-2026-11-08.md).
`select_nodes` first checks that the tile still matches those counts: the
pair count the caller expects, one pair with both ends in the tile, two pairs
with only the gateway end in it, and three pairs touching it, so none with
only the relayer end in it. The sites are then the both-ends pair's gateway
and each gateway-only pair's gateway, each gateway once with the both-ends
role first, and the receiver is the both-ends pair's relayer, each placed at
the centre of its box in EPSG:26915. The 2026-11-08 note's ranking
rule for its two busiest pairs is lost. By Casey's decision nothing selected
depends on a ranking.

Positions follow the contract's link-hour rules (`ingester.link_rules`):
a node's boxes are the boxes its pairs' counted receipts in kept hours used
under `lnk_02` (Casey, issue #44, 2026-11-17, and issue #55 comment
6985802330, item 2, (b)). A node is placed once per pair, at the box that
pair used (Casey: "a moving node is placed per pair"): one layer row per
distinct (node, box), whose id is the node's pseudonym when all its pairs
used one box and `<pseudonym>.<n>` otherwise, n counting its boxes from 1 in
the capture's pair order (`site_row_ids`). A row is placed at the midpoint of
its box's edges.

Each pair is judged at the tile edge by its own rows (Casey, issue #55
comment 6985802330, item 6): a row is `in` when its box lies inside the
tile, `out` when it lies outside and `split` when it crosses the tile edge,
the box being its four corners in EPSG:26915 (`row_state`). A pair whose
row straddles the tile edge, or whose placed row fails the DEM edge check,
is dropped, and only that pair: the node's other pairs, whose rows lie
inside, stay (Casey, issue #44, engine_inputs Q1 (a), as item 6 reads it
for rows). A pair whose kept hours used more than one box at an end
(`link_pairs.MovedPair`) has no row and is dropped the same way, with the
reason MORE_THAN_ONE_BOX (item 3).

The 2026-11-08 counts check runs first, over every pair, those with two boxes
included (item 3): there a node counts as touching the tile when any of its
boxes is `in` or `split`, and every pair it is in counts with that end in
the tile (item 6; `counted_states`). The kinds of the pairs, both-ends or
gateway-only, then follow each pair's own rows, a straddling row counted as
in; a pair with two boxes takes its kind from the counted states. Dropping
the both-ends pair or a gateway-only pair stops the run for Casey
(`Selection.layers`).

With the count check as it is, three touching pairs and all three selected,
an unselected pair can only be dropped if it touches the tile, and a fourth
touching pair fails the count check first. A drop that reaches the drop step
is therefore always of a selected pair, and stops the run. `straddle_drops`
finds the straddle and two-box drops before those checks run, so the command
can print them first (Casey, PR #51, (a)).

`select_sites` is the x72y429 run with no real receiver (Casey, 2026-11-17,
engine_inputs (c)): the same candidates and roles, but no stop on the count
check or a dropped pair, and no receiver. A gateway row is kept only when it
does not straddle the tile edge and passes the DEM edge check; the others are
returned with their reason.
`count_differences` lists every count that differs from 2026-11-08, so that
run can report them; `select_nodes` refuses on the first.
`site_straddle_drops` gives that run's straddle drops with their kinds.

`busiest_two` ranks pairs by link-hours, ties broken by receipts in those
link-hours (Casey, issue #44, engine_inputs Q2 (a), 2026-11-16; the
link-hours are now the merged snapshot's kept hours, where Casey's rule
counted them over both spike runs), and refuses when second and third
place are still equal on both. Casey keeps that ranking wherever one is needed later (issue #44,
busiest pair, 2026-11-17); the selection does not call it.

The DEM check uses the engine's rule: `engine.grid.run_grid` builds the grid
for one box plus the engine's 50 m margin, and the comparison of
`engine.terrain_build.check_covers` is mirrored on the DEM snapshot's bounds,
read through the engine's own snapshot reader. Nothing here prints, logs or
puts a coordinate in an error message.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import rasterio

from engine.config import SnapshotIds
from engine.errors import ConfigError
from engine.grid import run_grid
from engine.points import Endpoint
from engine.snapshots import resolve_snapshots
from ingester.link_cells import Edges, LinkBox
from ingester.link_pairs import (
    CaptureLinks,
    MovedPair,
    PairActivity,
    SelectionError,
)
from terrain.boxes import EdgeBox, PositionBox

LAYER_EPSG = 26915
BOTH_ENDS_GATEWAY = "both_ends_gateway"
GATEWAY_ONLY_GATEWAY = "gateway_only_gateway"
BOTH_ENDS_RELAYER = "both_ends_relayer"
BOTH_ENDS_PAIR = "both-ends pair"
GATEWAY_ONLY_PAIR = "gateway-only pair"
OTHER_PAIR = "other pair"
SITE_RULE = (
    "the in-tile gateway of every pair touching the tile, the both-ends pair's first "
    "(Casey, issue #44, busiest pair, 2026-11-17)"
)
# A drop line printed before the tile check names the kind only when the check
# would pass; when it would refuse, no pair is selected yet.
KIND_NOT_DETERMINED = "pair (kind not determined)"
STRADDLES = "straddles the tile edge"
FAILS_DEM = "fails the DEM edge check"
# Casey, issue #55 comment 6985802330, item 3: two-box pairs are dropped after
# the counts check, as for straddles.
MORE_THAN_ONE_BOX = "used more than one box in its kept hours"
IN, OUT, SPLIT = "in", "out", "split"
ENDS = ("relayer", "gateway")
TILE_SIZE_M = 10_000.0
TILE_PATTERN = re.compile(r"x(\d+)y(\d+)")
# The tile counts of 2026-11-08: one pair with both ends in the tile and two
# with only the gateway end in it, three pairs touched.
GATEWAY_ONLY_PAIRS = 2
PAIRS_TOUCHED = 3
# The grid's pixel size does not move its edges, which lie on 10 m multiples
# whatever the size; 1 m is the test tile DEM's.
DEM_RESOLUTION_M = 1.0

Extent = tuple[float, float, float, float]
"""West, south, east, north in EPSG:26915 metres."""
AnyPair = PairActivity | MovedPair
"""A pair with one box per end, or one to drop for an end with more than one."""
PairState = tuple[str, str]
"""Where a pair's relayer and gateway lie against the tile: `in`, `out` or `split` each."""


@dataclass(frozen=True)
class Tile:
    """A USGS 1 m DEM tile: xAyB covers easting A to A+1 and northing B-1 to B, 10 km units."""

    name: str
    west_m: float
    south_m: float
    east_m: float
    north_m: float

    def contains(self, easting_m: float, northing_m: float) -> bool:
        """Whether a point in EPSG:26915 lies in the tile; west and south edges inclusive."""
        inside_x = self.west_m <= easting_m < self.east_m
        return inside_x and self.south_m <= northing_m < self.north_m


@dataclass(frozen=True)
class PlacedNode:
    """A selected node with the `lnk_02` box its pairs used, placed at the box's centre.

    Attributes:
        pseudonym: The node's pseudonym as stored in the snapshot.
        role: `both_ends_gateway`, `gateway_only_gateway` or `both_ends_relayer`.
        box: The `lnk_02` box, whose four edges the layer row carries (ADR 0008).
        precise_source: Whether the node is a precise MeshSTL source (`pos_04`).
        row_id: The layer row's id when it is not the pseudonym (`site_row_ids`).
    """

    pseudonym: str
    role: str
    box: LinkBox
    precise_source: bool
    row_id: str | None = None

    @property
    def edge_box(self) -> EdgeBox:
        """The box as the engine reads it from the layer's four edges."""
        return edge_box(self.box)

    @property
    def easting_m(self) -> float:
        """Box centre easting, EPSG:26915 metres (`EdgeBox.centre_xy_m`)."""
        return self.edge_box.centre_xy_m[0]

    @property
    def northing_m(self) -> float:
        """Box centre northing, EPSG:26915 metres (`EdgeBox.centre_xy_m`)."""
        return self.edge_box.centre_xy_m[1]

    @property
    def layer_id(self) -> str:
        """The `site_id` or `rx_id` the row is written with."""
        return self.row_id or self.pseudonym


@dataclass(frozen=True)
class DroppedPair:
    """A pair the run drops, and why.

    Attributes:
        pair: The pair.
        kind: BOTH_ENDS_PAIR or GATEWAY_ONLY_PAIR when the run selected it, else OTHER_PAIR.
        reasons: (end, reason) for each end that drops it, relayer first; the
            end is `relayer` or `gateway`, the reason STRADDLES, FAILS_DEM or
            MORE_THAN_ONE_BOX.
    """

    pair: AnyPair
    kind: str
    reasons: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Selection:
    """The tile's placed nodes, their DEM edge check and the pairs dropped.

    Attributes:
        placed: The selected nodes that do not straddle, at their boxes'
            centres: the sites in order, then the receiver.
        dem_check: Per placed row's `layer_id`, whether its box plus the
            engine's margin lies inside the DEM (`inside_dem`).
        dropped: The dropped pairs, in the capture's pair order.
    """

    placed: tuple[PlacedNode, ...]
    dem_check: dict[str, bool]
    dropped: tuple[DroppedPair, ...]

    def layers(self) -> tuple[list[PlacedNode], PlacedNode | None]:
        """The site rows and the receiver row, unless a selected pair was dropped.

        The receiver is None when no pair has both ends in the tile by its own
        rows: Casey (PR #70 comment 6992903491), (b), "do not stop; write the sites with no
        receiver layer".

        Raises:
            SelectionError: when the both-ends pair or a gateway-only pair was
                dropped: the run stops for Casey (issue #44, engine_inputs Q1 (a)).
        """
        stopped = [dropped.kind for dropped in self.dropped if dropped.kind != OTHER_PAIR]
        if stopped:
            raise SelectionError(
                f"selected pairs dropped: {', '.join(stopped)}; Casey decides before "
                "engine.prepare (issue #44, engine_inputs Q1 (a))"
            )
        sites = [node for node in self.placed if node.role != BOTH_ENDS_RELAYER]
        receivers = [node for node in self.placed if node.role == BOTH_ENDS_RELAYER]
        return sites, receivers[0] if receivers else None


def parse_tile(name: str) -> Tile:
    """The tile of a USGS 1 m tile name.

    Args:
        name: A name such as x72y429.

    Returns:
        The tile's edges in EPSG:26915 metres.

    Raises:
        SelectionError: if the name is not of the form xAyB.
    """
    match = TILE_PATTERN.fullmatch(name)
    if match is None:
        raise SelectionError(f"tile {name!r} is not a USGS 1 m tile name such as x72y429")
    west_m = int(match.group(1)) * TILE_SIZE_M
    north_m = int(match.group(2)) * TILE_SIZE_M
    return Tile(name, west_m, north_m - TILE_SIZE_M, west_m + TILE_SIZE_M, north_m)


BUSIEST_RULE = (
    "by link-hours, ties broken by receipts in those link-hours "
    "(Casey, issue #44, engine_inputs Q2 (a), 2026-11-16)"
)


def _busyness(pair: PairActivity) -> tuple[int, int]:
    return pair.link_hours, pair.receipts


def busiest_two(pairs: Sequence[PairActivity]) -> tuple[PairActivity, PairActivity]:
    """The two busiest pairs under BUSIEST_RULE, kept for a later ranking; no site depends on it.

    Args:
        pairs: Every pair of the capture.

    Returns:
        The two pairs, busiest first.

    Raises:
        SelectionError: with fewer than two pairs, or when second and third
            place are equal on both link-hours and receipts.
    """
    if len(pairs) < 2:
        raise SelectionError(f"{len(pairs)} pairs; the ranking needs two pairs")
    first, second, *rest = sorted(pairs, key=_busyness, reverse=True)
    if rest and _busyness(second) == _busyness(rest[0]):
        raise SelectionError("a tie for second busiest pair on link-hours and receipts")
    return first, second


def dem_extent(root: Path, snapshot_id: str) -> Extent:
    """The bounds of a DEM snapshot's tiles, read as the engine reads them.

    Args:
        root: The snapshots folder.
        snapshot_id: The DEM snapshot's id.

    Returns:
        West, south, east and north in the DEM's CRS, EPSG:26915 for the
        engine's DEMs.

    Raises:
        SelectionError: if the engine's snapshot reader refuses the snapshot.
    """
    ids = SnapshotIds(dem=snapshot_id, dsm=None, canopy=None, buildings=None, p1812_maps=None)
    try:
        dem = resolve_snapshots(ids, root)["dem"]
    except ConfigError as err:
        raise SelectionError(str(err)) from err
    bounds = []
    for path in dem.rasters():
        with rasterio.open(path) as tile:
            bounds.append(tile.bounds)
    return (
        min(b.left for b in bounds),
        min(b.bottom for b in bounds),
        max(b.right for b in bounds),
        max(b.top for b in bounds),
    )


def inside_dem(box: PositionBox, extent: Extent) -> bool:
    """Whether the engine's run grid for one box lies inside the DEM extent.

    A placed node's box is its whole `lnk_02` box (`PlacedNode.edge_box`), so
    the check covers its four corners, not the square inscribed in it (ADR
    0008, the stricter check).

    Args:
        box: The box as the engine reads it, EPSG:26915 metres.
        extent: The DEM's west, south, east and north edges, from `dem_extent`.

    Returns:
        False exactly when engine.terrain_build.check_covers would refuse a
        run with this box alone.
    """
    grid = run_grid([Endpoint(endpoint_id="check", box=box)], resolution_m=DEM_RESOLUTION_M)
    west, south, east, north = extent
    outside = grid.west_m < west or grid.south_m < south
    return not (outside or grid.east_m > east or grid.north_m > north)


def edge_box(box: LinkBox) -> EdgeBox:
    """A `lnk_02` box as the engine's `EdgeBox`, its four edges unchanged (ADR 0008)."""
    return EdgeBox(south_i=box.south_i, north_i=box.north_i, west_i=box.west_i, east_i=box.east_i)


def box_extent_m(box: LinkBox) -> Extent:
    """West, south, east and north of a box's four corners in EPSG:26915 metres."""
    return edge_box(box).extent_m()


def row_state(box: LinkBox, tile: Tile) -> str:
    """`in`, `out` or `split` for a row's box against the tile (Casey, item 6).

    `in` when the box lies inside the tile, `out` when it lies outside, and
    `split` when it crosses the tile edge, the box being its four corners in
    EPSG:26915. Box and tile are both half-open, west and south included, east
    and north excluded, so a box that only touches the tile edge does not
    straddle it, as touching is not overlap (`lnk_02`).
    """
    west, south, east, north = box_extent_m(box)
    inside_x = tile.west_m <= west and east <= tile.east_m
    if inside_x and tile.south_m <= south and north <= tile.north_m:
        return IN
    beside = east <= tile.west_m or west >= tile.east_m
    if beside or north <= tile.south_m or south >= tile.north_m:
        return OUT
    return SPLIT


def pair_states(links: CaptureLinks, tile: Tile) -> dict[PairActivity, PairState]:
    """Where each pair's relayer row and gateway row lie against the tile (`row_state`).

    Raises:
        SelectionError: if a node has no box.
    """
    return {
        pair: (
            row_state(end_box(links, pair, "relayer"), tile),
            row_state(end_box(links, pair, "gateway"), tile),
        )
        for pair in links.pairs
    }


def capture_order(links: CaptureLinks) -> list[AnyPair]:
    """Every pair of the capture in its order (relayer, then gateway), two-box pairs last."""
    return [*links.pairs, *links.moved_pairs]


def _touching(links: CaptureLinks, tile: Tile) -> set[str]:
    """The nodes with any box in or across the tile: their rows and their two-box pairs' boxes."""
    boxes: dict[str, set[LinkBox]] = {node: set(found.boxes) for node, found in links.nodes.items()}
    for pair in links.pairs:
        for end, node in zip(ENDS, (pair.relayer, pair.gateway), strict=True):
            boxes.setdefault(node, set()).add(end_box(links, pair, end))
    for moved in links.moved_pairs:
        boxes.setdefault(moved.relayer, set()).update(moved.relayer_boxes)
        boxes.setdefault(moved.gateway, set()).update(moved.gateway_boxes)
    return {node for node, found in boxes.items() if any(row_state(b, tile) != OUT for b in found)}


def counted_states(links: CaptureLinks, tile: Tile) -> dict[AnyPair, PairState]:
    """The states the 2026-11-08 counts check reads, over every pair, two-box pairs included.

    Each end is `in` when its node has any box in or across the tile (Casey,
    issue #55 comment 6985802330, items 3 and 6), else `out`.

    Raises:
        SelectionError: if a node has no box.
    """
    touching = _touching(links, tile)

    def end(node: str) -> str:
        return IN if node in touching else OUT

    return {pair: (end(pair.relayer), end(pair.gateway)) for pair in capture_order(links)}


def _counted(state: PairState) -> PairState:
    """The state a pair's kind reads: a straddling row counts as in the tile."""
    relayer, gateway = (IN if end == SPLIT else end for end in state)
    return relayer, gateway


def _kind_of(state: PairState) -> str:
    counted = _counted(state)
    if counted == (IN, IN):
        return BOTH_ENDS_PAIR
    return GATEWAY_ONLY_PAIR if counted == (OUT, IN) else OTHER_PAIR


@dataclass(frozen=True)
class KindConflict:
    """A pair whose kind by its own rows differs from its kind in the counts check.

    Attributes:
        pair: The pair.
        by_rows: BOTH_ENDS_PAIR, GATEWAY_ONLY_PAIR or OTHER_PAIR by its own rows.
        counted: The same, by `counted_states`.
    """

    pair: PairActivity
    by_rows: str
    counted: str


def kind_conflicts(links: CaptureLinks, tile: Tile) -> tuple[KindConflict, ...]:
    """The pairs whose kind by their own rows differs from their kind in the counts check.

    Casey (issue #55 comment 6990646633, item 3): each pair's own row decides its
    kind and its gateway's role. A moving node near the tile edge can make the two disagree, one of
    its pairs' rows inside the tile and another outside, so every such pair is
    listed in the local run report for Casey in the session, and no run stops
    for it. A two-box pair has no row and takes its kind from the counted
    states, so it never disagrees.

    Raises:
        SelectionError: if a node has no box.
    """
    rows = pair_states(links, tile)
    counted = counted_states(links, tile)
    found = []
    for pair in links.pairs:
        by_rows, by_counts = _kind_of(rows[pair]), _kind_of(counted[pair])
        if by_rows != by_counts:
            found.append(KindConflict(pair, by_rows, by_counts))
    return tuple(found)


def tile_counts(states: Mapping[AnyPair, PairState]) -> dict[str, int]:
    """Pairs with both ends, only the gateway end, only the relayer end in the tile, and touched.

    A `split` end counts as in the tile (Casey, issue #44, engine_inputs Q1 (a), 2026-11-17).
    """
    values = [_counted(state) for state in states.values()]
    return {
        "both ends": values.count((IN, IN)),
        "only the gateway end": values.count((OUT, IN)),
        "only the relayer end": values.count((IN, OUT)),
        "touched": sum(1 for state in values if IN in state),
    }


def _drop_reason(row: str, state: str, dem_check: dict[str, bool]) -> str | None:
    if state == SPLIT:
        return STRADDLES
    if dem_check.get(row) is False:
        return FAILS_DEM
    return None


RowOf = Callable[[PairActivity, str], str]
"""The layer row id of a pair's `relayer` or `gateway` end."""


def dropped_pairs(
    states: dict[PairActivity, PairState],
    kinds: Mapping[AnyPair, str],
    dem_check: dict[str, bool],
    other_kind: str = OTHER_PAIR,
    row_of: RowOf | None = None,
) -> tuple[DroppedPair, ...]:
    """The pairs dropped under engine_inputs Q1 (a), each with every end that drops it.

    Args:
        states: Every pair's state from `pair_states`.
        kinds: BOTH_ENDS_PAIR or GATEWAY_ONLY_PAIR for each selected pair.
        dem_check: Per row the run places, whether it passes the DEM edge check.
        other_kind: The kind of a pair not in kinds.
        row_of: The row id of a pair end; the end's pseudonym when None.

    Returns:
        A pair is dropped when either end's row straddles the tile edge, or
        the row the run places for an end fails the DEM edge check; the other
        pairs are not listed. Two-box pairs are `moved_drops`.
    """
    dropped = []
    for pair, state in states.items():
        nodes = (pair.relayer, pair.gateway)
        rows = [
            node if row_of is None else row_of(pair, end)
            for end, node in zip(ENDS, nodes, strict=True)
        ]
        reasons = tuple(
            (end, reason)
            for end, row, node_state in zip(ENDS, rows, state, strict=True)
            if (reason := _drop_reason(row, node_state, dem_check)) is not None
        )
        if reasons:
            dropped.append(DroppedPair(pair, kinds.get(pair, other_kind), reasons))
    return tuple(dropped)


def moved_drops(
    links: CaptureLinks, kinds: Mapping[AnyPair, str], other_kind: str = OTHER_PAIR
) -> tuple[DroppedPair, ...]:
    """The two-box pairs, each dropped with MORE_THAN_ONE_BOX at the ends that used two."""
    return tuple(
        DroppedPair(
            moved, kinds.get(moved, other_kind), tuple((e, MORE_THAN_ONE_BOX) for e in moved.ends)
        )
        for moved in links.moved_pairs
    )


def _all_drops(
    links: CaptureLinks,
    states: dict[PairActivity, PairState],
    kinds: Mapping[AnyPair, str],
    dem_check: dict[str, bool],
    other_kind: str = OTHER_PAIR,
    row_of: RowOf | None = None,
) -> tuple[DroppedPair, ...]:
    """Straddle and DEM drops in the capture's pair order, then the two-box drops."""
    return (
        *dropped_pairs(states, kinds, dem_check, other_kind, row_of),
        *moved_drops(links, kinds, other_kind),
    )


def straddle_drops(links: CaptureLinks, tile: Tile, expect_pairs: int) -> tuple[DroppedPair, ...]:
    """The pairs a straddling row or two boxes drop, found before the tile check can refuse.

    Casey, PR #51, (a): the drop lines print before the count checks, so a
    refusal shows which pair and which end caused it. The DEM edge check needs
    a placed row, so its drops come later, from `select_nodes`.

    Args:
        links: The capture's pairs and node positions.
        tile: The engine run's tile.
        expect_pairs: The pairs the contract's rules give on the snapshot.

    Returns:
        Each pair with a straddling row or an end with two boxes, with its
        kind when every check of `select_nodes` before placement would pass
        (the pair count and the 2026-11-08 tile counts), else
        KIND_NOT_DETERMINED.

    Raises:
        SelectionError: if a node has no box.
    """
    states = pair_states(links, tile)
    kinds = _tile_check(links, tile, expect_pairs)
    if isinstance(kinds, str):
        return _all_drops(links, states, {}, {}, other_kind=KIND_NOT_DETERMINED)
    return _all_drops(links, states, kinds, {})


def end_box(links: CaptureLinks, pair: PairActivity, end: str) -> LinkBox:
    """The box a pair used for its `relayer` or `gateway` end.

    A pair from `link_pairs.capture_links` always carries both boxes; a pair
    without one takes its node's only box.

    Raises:
        SelectionError: if the node has no box, or the pair carries none and
            the node has more than one; the message names no node.
    """
    box = pair.relayer_box if end == "relayer" else pair.gateway_box
    if box is not None:
        return box
    found = links.nodes.get(pair.relayer if end == "relayer" else pair.gateway)
    if found is None:
        raise SelectionError("a node of a pair has no box")
    if found.one_box is None:
        raise SelectionError(
            f"a pair carries no {end} box and that node used {len(found.boxes)}; the rows "
            "need the box each pair used (lnk_02)"
        )
    return found.one_box


def site_row_ids(links: CaptureLinks) -> dict[tuple[str, Edges], str]:
    """The layer row id of each (node, box edges) the pairs used.

    A node whose pairs all used one box keeps its pseudonym. A node with more
    than one box gets `<pseudonym>.<n>`, n counting its boxes from 1 in the
    order the capture's pairs (sorted by relayer, then gateway; relayer end
    first) first use them, so the id says nothing of where a box lies. The
    engine takes such ids: `site_id` matches `engine.run.SITE_ID` and every id
    in a layer is distinct (`engine.points.read_points`).

    Raises:
        SelectionError: if a pair carries no box for an end (`end_box`).
    """
    order: dict[str, list[Edges]] = {}
    for pair in links.pairs:
        for end, node in zip(ENDS, (pair.relayer, pair.gateway), strict=True):
            edges = end_box(links, pair, end).edges
            seen = order.setdefault(node, [])
            if edges not in seen:
                seen.append(edges)
    ids: dict[tuple[str, Edges], str] = {}
    for node, boxes in order.items():
        found = links.nodes.get(node)
        several = len(boxes) > 1 or (found is not None and found.one_box is None)
        for n, edges in enumerate(boxes, start=1):
            ids[(node, edges)] = f"{node}.{n}" if several else node
    return ids


def _row_of(links: CaptureLinks, ids: dict[tuple[str, Edges], str]) -> RowOf:
    def row_of(pair: PairActivity, end: str) -> str:
        node = pair.relayer if end == "relayer" else pair.gateway
        return ids[(node, end_box(links, pair, end).edges)]

    return row_of


SiteRow = tuple[str, str, str, LinkBox]
"""A layer row to place: its id, the node's pseudonym, its role and the box."""


def _place(links: CaptureLinks, row: SiteRow) -> PlacedNode:
    row_id, node, role, box = row
    precise = links.nodes[node].precise_source if node in links.nodes else False
    return PlacedNode(node, role, box, precise, None if row_id == node else row_id)


def count_differences(
    links: CaptureLinks, states: Mapping[AnyPair, PairState], tile: Tile, expect_pairs: int
) -> list[str]:
    """Each way the tile differs from the 2026-11-08 counts, in the order the checks run.

    Args:
        links: The capture's pairs.
        states: Every pair's state from `counted_states`.
        tile: The engine run's tile.
        expect_pairs: The union of pairs the tile check counted, 8 on 2026-11-08.

    Returns:
        One message per count that differs: the pair count, one pair with both
        ends in the tile, two with only the gateway end, three touching. Empty
        when the tile matches.
    """
    counts = tile_counts(states)
    pairs = links.pair_count
    both, gateway_only = counts["both ends"], counts["only the gateway end"]
    checks = (
        (pairs == expect_pairs, f"{pairs} pairs, not the {expect_pairs} expected"),
        (both == 1, f"{both} pairs have both ends in {tile.name}, not one"),
        (
            gateway_only == GATEWAY_ONLY_PAIRS,
            f"{gateway_only} pairs have only the gateway end in {tile.name}, not two",
        ),
        (counts["touched"] == PAIRS_TOUCHED, f"pairs touching {tile.name} are not {PAIRS_TOUCHED}"),
    )
    return [message for passed, message in checks if not passed]


def _gateway_pairs(states: Mapping[AnyPair, PairState]) -> dict[AnyPair, str]:
    """Every pair whose gateway counts as in the tile, by kind.

    The pairs with both ends in the tile come first, then those with only the
    gateway end, each in the order of states; a `split` end counts as in.
    """
    counted = {pair: _counted(state) for pair, state in states.items()}
    both = [pair for pair, state in counted.items() if state == (IN, IN)]
    gateway_only = [pair for pair, state in counted.items() if state == (OUT, IN)]
    return {**dict.fromkeys(both, BOTH_ENDS_PAIR), **dict.fromkeys(gateway_only, GATEWAY_ONLY_PAIR)}


def _kinds(links: CaptureLinks, tile: Tile) -> dict[AnyPair, str]:
    """Each pair's kind by its own rows; a two-box pair's by the counted states (item 3)."""
    rows = pair_states(links, tile)
    counted = counted_states(links, tile)
    by_rows: dict[AnyPair, PairState] = {
        pair: rows[pair] if isinstance(pair, PairActivity) else state
        for pair, state in counted.items()
    }
    return _gateway_pairs(by_rows)


def _tile_check(links: CaptureLinks, tile: Tile, expect_pairs: int) -> dict[AnyPair, str] | str:
    """The pairs touching the tile by kind, or the reason the 2026-11-08 counts refuse.

    The counts read `counted_states`, every pair included; the kinds read
    each pair's own rows (`_kinds`). The reason, the first count that
    differs, is returned, not raised, so `straddle_drops` can ask without
    catching anything; `select_nodes` raises it.
    """
    differences = count_differences(links, counted_states(links, tile), tile, expect_pairs)
    if differences:
        return differences[0]
    return _kinds(links, tile)


def _site_rows(
    links: CaptureLinks, kinds: Mapping[AnyPair, str], ids: dict[tuple[str, Edges], str]
) -> list[SiteRow]:
    """One row per gateway box the selected pairs used, in the order of kinds, both-ends first.

    A gateway that used one box in every selected pair is one row, with the
    role of its first pair; a gateway at two places is a row per place.
    """
    rows: dict[str, SiteRow] = {}
    for pair, kind in kinds.items():
        if not isinstance(pair, PairActivity):
            continue
        box = end_box(links, pair, "gateway")
        row_id = ids[(pair.gateway, box.edges)]
        role = BOTH_ENDS_GATEWAY if kind == BOTH_ENDS_PAIR else GATEWAY_ONLY_GATEWAY
        rows.setdefault(row_id, (row_id, pair.gateway, role, box))
    return list(rows.values())


def _rows(
    links: CaptureLinks, kinds: Mapping[AnyPair, str], ids: dict[tuple[str, Edges], str]
) -> list[SiteRow]:
    """The site rows, then the receiver: the both-ends pair's relayer at the box that pair used.

    A both-ends pair with two boxes gives no receiver row; it is dropped, and
    `Selection.layers` refuses. When the counts check passes but no pair has
    both ends in the tile by its own rows, no row is the receiver either, and
    the run writes the sites with no receiver layer (Casey, PR #70 comment 6992903491, (b)).
    """
    sites = _site_rows(links, kinds, ids)
    both = [p for p, kind in kinds.items() if kind == BOTH_ENDS_PAIR]
    if not both or not isinstance(both[0], PairActivity):
        return sites
    pair = both[0]
    box = end_box(links, pair, "relayer")
    return [*sites, (ids[(pair.relayer, box.edges)], pair.relayer, BOTH_ENDS_RELAYER, box)]


def select_nodes(links: CaptureLinks, tile: Tile, expect_pairs: int, extent: Extent) -> Selection:
    """The tile's real sites and receiver by tile membership, and the pairs dropped.

    Args:
        links: The capture's pairs and node positions.
        tile: The engine run's tile.
        expect_pairs: The union of pairs the tile check counted, 8 on 2026-11-08.
        extent: The DEM's edges in EPSG:26915 metres, from `dem_extent`.

    Returns:
        The placed rows (the both-ends pair's gateway, then the gateway-only
        pairs' gateways in the capture's pair order, a row per gateway box
        the selected pairs used, the both-ends role first, then the
        both-ends pair's relayer at that pair's box), less any row that
        straddles the tile edge, with their DEM edge check and the dropped
        pairs. No busiest-pair ranking enters the choice (SITE_RULE).
        `Selection.layers` refuses when a selected pair was dropped.

    Raises:
        SelectionError: when the pairs do not reproduce the 2026-11-08 tile
            counts, or a node of a pair has no box.
    """
    # The pair count refuses before pair_states, so a call with a wrong count
    # and a node with no box names the count. The command calls
    # pair_states earlier, for its tile line, and refuses on the position.
    if links.pair_count != expect_pairs:
        raise SelectionError(f"{links.pair_count} pairs, not the {expect_pairs} expected")
    states = pair_states(links, tile)
    kinds = _tile_check(links, tile, expect_pairs)
    if isinstance(kinds, str):
        raise SelectionError(kinds)
    ids = site_row_ids(links)
    rows = _rows(links, kinds, ids)
    placed = tuple(_place(links, row) for row in rows if row_state(row[3], tile) != SPLIT)
    dem_check = _dem_check(placed, extent)
    dropped = _all_drops(links, states, kinds, dem_check, row_of=_row_of(links, ids))
    return Selection(placed, dem_check, dropped)


def _dem_check(placed: Sequence[PlacedNode], extent: Extent) -> dict[str, bool]:
    return {node.layer_id: inside_dem(node.edge_box, extent) for node in placed}


@dataclass(frozen=True)
class LeftOutSite:
    """An in-tile gateway a sites-only run does not write, and why.

    Attributes:
        pseudonym: The gateway's pseudonym.
        role: The role it would have had, `both_ends_gateway` or `gateway_only_gateway`.
        reason: STRADDLES or FAILS_DEM.
        row_id: The row's id when it is not the pseudonym (`site_row_ids`).
    """

    pseudonym: str
    role: str
    reason: str
    row_id: str | None = None

    @property
    def layer_id(self) -> str:
        return self.row_id or self.pseudonym


@dataclass(frozen=True)
class SiteSelection:
    """The sites of a sites-only run, the gateways left out and the pairs dropped.

    Attributes:
        selection: The gateway rows placed (not straddling), their DEM edge
            check and the pairs dropped; no receiver is placed.
        left_out: The gateway rows not written, in site order.
    """

    selection: Selection
    left_out: tuple[LeftOutSite, ...]

    def sites(self) -> list[PlacedNode]:
        """The placed gateways that pass the DEM edge check, in site order.

        Raises:
            SelectionError: when no site is left.
        """
        if not self.selection.placed and not self.left_out:
            raise SelectionError("no site: no pair touching the tile has its gateway in it")
        kept = [node for node in self.selection.placed if self.selection.dem_check[node.layer_id]]
        if not kept:
            raise SelectionError(
                f"no site left; all {len(self.left_out)} in-tile gateways were left out"
            )
        return kept


def site_straddle_drops(links: CaptureLinks, tile: Tile) -> tuple[DroppedPair, ...]:
    """The pairs a straddling row or two boxes drop in a sites-only run, each with its kind.

    A sites-only run never refuses on the 2026-11-08 counts, so the kind is
    known before they are checked: `both-ends pair` or `gateway-only pair`
    by the pair's own rows, as `select_sites` reads them, `other pair`
    otherwise.

    Raises:
        SelectionError: if a node has no box.
    """
    return _all_drops(links, pair_states(links, tile), _kinds(links, tile), {})


def _place_sites(
    links: CaptureLinks, rows: list[SiteRow], tile: Tile, extent: Extent
) -> tuple[tuple[PlacedNode, ...], dict[str, bool], tuple[LeftOutSite, ...]]:
    """Place each gateway row that does not straddle; the rest, and DEM failures, left out."""
    straddling = {row[0] for row in rows if row_state(row[3], tile) == SPLIT}
    placed = tuple(_place(links, row) for row in rows if row[0] not in straddling)
    dem_check = _dem_check(placed, extent)
    reasons = dict.fromkeys(straddling, STRADDLES)
    reasons.update({row_id: FAILS_DEM for row_id, inside in dem_check.items() if not inside})
    left_out = tuple(
        LeftOutSite(node, role, reasons[row_id], None if row_id == node else row_id)
        for row_id, node, role, _ in rows
        if row_id in reasons
    )
    return placed, dem_check, left_out


def select_sites(links: CaptureLinks, tile: Tile, extent: Extent) -> SiteSelection:
    """The tile's real sites alone, for the x72y429 run with no real receiver.

    Casey, 2026-11-17, engine_inputs (c): the site rule of `select_nodes`
    without its count check, its stop on a dropped pair or its receiver. The
    candidates are a row per gateway box that a pair whose gateway row is in
    or across the tile used, the both-ends role first; roles follow each
    pair's own rows, a straddling row counted as in. A row is written only
    when it does not straddle the tile edge and passes the DEM edge check.
    The relayers are not placed, so only a gateway's row can drop a pair at
    the DEM edge check.

    Args:
        links: The capture's pairs and node positions.
        tile: The engine run's tile.
        extent: The DEM's edges in EPSG:26915 metres, from `dem_extent`.

    Returns:
        The placed gateways with their DEM edge check, the pairs dropped
        under engine_inputs Q1 (a) and the gateways left out with their
        reasons. `SiteSelection.sites` refuses when no site is left.

    Raises:
        SelectionError: if a node of a pair has no box.
    """
    states = pair_states(links, tile)
    kinds = _kinds(links, tile)
    ids = site_row_ids(links)
    rows = _site_rows(links, kinds, ids)
    placed, dem_check, left_out = _place_sites(links, rows, tile, extent)
    dropped = _all_drops(links, states, kinds, dem_check, row_of=_row_of(links, ids))
    return SiteSelection(Selection(placed, dem_check, dropped), left_out)
