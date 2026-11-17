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
its stationary box centre in EPSG:26915. The 2026-11-08 note's ranking rule
for its two busiest pairs is lost. By Casey's decision nothing selected
depends on a ranking.

A node straddles the tile when its usable boxes lie on both sides of the tile
edge; it has no stationary box. The rule for it (Casey, issue #44,
engine_inputs Q1 (a), 2026-11-17): it is treated like a node that fails the
DEM edge check, and only its own pair is dropped, after the check that the
tile still matches the 2026-11-08 counts, with a straddling node counted as
touching the tile. So the counts run first, over every pair, with a
straddling node counted as in the tile. Then a pair is dropped when either of
its nodes straddles, or a node of it that the run places fails the DEM edge
check. A node shared with another pair stays in that pair unless it fails
there too. Dropping the both-ends pair or a gateway-only pair stops the run
for Casey (`Selection.layers`). A selected node that does not straddle but
still has no stationary box, two boxes inside the tile for example, is
refused as before.

With the count check as it is, three touching pairs and all three selected,
an unselected pair can only be dropped if it touches the tile, and a fourth
touching pair fails the count check first. A drop that reaches the drop step
is therefore always of a selected pair, and stops the run. `straddle_drops`
finds the straddle drops before those checks run, so the command can print
them first (Casey, PR #51, (a)).

`busiest_two` ranks pairs by link-hours over both runs, ties broken by
receipts in those link-hours (Casey, issue #44, engine_inputs Q2 (a),
2026-11-16), and refuses when second and third place are still equal on
both. Casey keeps that ranking wherever one is needed later (issue #44,
busiest pair, 2026-11-17); the selection does not call it.

The DEM check uses the engine's rule: `engine.grid.run_grid` builds the grid
for one box plus the engine's 50 m margin, and the comparison of
`engine.terrain_build.check_covers` is mirrored on the DEM snapshot's bounds,
read through the engine's own snapshot reader. Nothing here prints, logs or
puts a coordinate in an error message.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import rasterio
from pyproj import Transformer

from engine.config import SnapshotIds
from engine.errors import ConfigError
from engine.grid import run_grid
from engine.points import Endpoint
from engine.snapshots import resolve_snapshots
from ingester.link_pairs import (
    USABLE_BITS,
    CaptureLinks,
    NodePositions,
    PairActivity,
    SelectionError,
    box_half_width_m,
)
from terrain.draws import EndpointBox

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
_TO_METRES = Transformer.from_crs(4326, LAYER_EPSG, always_xy=True)

Extent = tuple[float, float, float, float]
"""West, south, east, north in EPSG:26915 metres."""
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
    """A selected node at its stationary box centre.

    Attributes:
        pseudonym: The node's pseudonym as stored in the snapshot.
        role: `both_ends_gateway`, `gateway_only_gateway` or `both_ends_relayer`.
        easting_m: Box centre easting, EPSG:26915 metres.
        northing_m: Box centre northing, EPSG:26915 metres.
        half_width_m: Box half-width in metres, from `box_half_width_m`.
        precise_source: Whether the node is a precise MeshSTL source (`pos_04`).
    """

    pseudonym: str
    role: str
    easting_m: float
    northing_m: float
    half_width_m: float
    precise_source: bool


@dataclass(frozen=True)
class DroppedPair:
    """A pair the run drops, and why.

    Attributes:
        pair: The pair.
        kind: BOTH_ENDS_PAIR or GATEWAY_ONLY_PAIR when the run selected it, else OTHER_PAIR.
        reasons: (end, reason) for each end that drops it, relayer first; the
            end is `relayer` or `gateway`, the reason STRADDLES or FAILS_DEM.
    """

    pair: PairActivity
    kind: str
    reasons: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Selection:
    """The tile's placed nodes, their DEM edge check and the pairs dropped.

    Attributes:
        placed: The selected nodes that do not straddle, at their stationary
            box centres: the sites in order, then the receiver.
        dem_check: Per placed node's pseudonym, whether its box plus the
            engine's margin lies inside the DEM (`inside_dem`).
        dropped: The dropped pairs, in the capture's pair order.
    """

    placed: tuple[PlacedNode, ...]
    dem_check: dict[str, bool]
    dropped: tuple[DroppedPair, ...]

    def layers(self) -> tuple[list[PlacedNode], PlacedNode]:
        """The site rows and the receiver row, unless a selected pair was dropped.

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
        return sites, receivers[0]


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
    "by link-hours over both runs, ties broken by receipts in those link-hours "
    "(Casey, issue #44, engine_inputs Q2 (a), 2026-11-16)"
)


def _busyness(pair: PairActivity) -> tuple[int, int]:
    return pair.link_hours_union, pair.receipts


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


def inside_dem(centre_xy_m: tuple[float, float], half_width_m: float, extent: Extent) -> bool:
    """Whether the engine's run grid for one box lies inside the DEM extent.

    Args:
        centre_xy_m: Box centre in EPSG:26915 metres.
        half_width_m: Box half-width in metres.
        extent: The DEM's west, south, east and north edges, from `dem_extent`.

    Returns:
        False exactly when engine.terrain_build.check_covers would refuse a
        run with this box alone.
    """
    box = EndpointBox(centre_xy_m=centre_xy_m, half_width_m=half_width_m)
    grid = run_grid([Endpoint(endpoint_id="check", box=box)], resolution_m=DEM_RESOLUTION_M)
    west, south, east, north = extent
    outside = grid.west_m < west or grid.south_m < south
    return not (outside or grid.east_m > east or grid.north_m > north)


def _project(latitude_i: int, longitude_i: int) -> tuple[float, float]:
    easting_m, northing_m = _TO_METRES.transform(longitude_i / 1e7, latitude_i / 1e7)
    return float(easting_m), float(northing_m)


def _node_state(nodes: dict[str, NodePositions], pseudonym: str, tile: Tile) -> str:
    """`in`, `out` or `split` for where a node's usable positions lie against the tile."""
    found = nodes.get(pseudonym)
    usable = [] if found is None else [b for b in found.boxes if b.precision_bits >= USABLE_BITS]
    inside = {tile.contains(*_project(box.latitude_i, box.longitude_i)) for box in usable}
    if not inside:
        raise SelectionError("a node of a pair has no usable position")
    if len(inside) == 2:
        return SPLIT
    return IN if inside == {True} else OUT


def pair_states(links: CaptureLinks, tile: Tile) -> dict[PairActivity, PairState]:
    """Where each pair's relayer and gateway lie against the tile, `split` for a straddling node.

    Raises:
        SelectionError: if a node has no usable position.
    """
    return {
        pair: (
            _node_state(links.nodes, pair.relayer, tile),
            _node_state(links.nodes, pair.gateway, tile),
        )
        for pair in links.pairs
    }


def _counted(state: PairState) -> PairState:
    """The state the tile check counts: a straddling node counts as in the tile (Q1 (a))."""
    relayer, gateway = (IN if end == SPLIT else end for end in state)
    return relayer, gateway


def tile_counts(states: dict[PairActivity, PairState]) -> dict[str, int]:
    """Pairs with both ends, only the gateway end, only the relayer end in the tile, and touched.

    A straddling node counts as in the tile (Casey, issue #44, engine_inputs Q1 (a), 2026-11-17).
    """
    values = [_counted(state) for state in states.values()]
    return {
        "both ends": values.count((IN, IN)),
        "only the gateway end": values.count((OUT, IN)),
        "only the relayer end": values.count((IN, OUT)),
        "touched": sum(1 for state in values if IN in state),
    }


def _drop_reason(node: str, state: str, dem_check: dict[str, bool]) -> str | None:
    if state == SPLIT:
        return STRADDLES
    if dem_check.get(node) is False:
        return FAILS_DEM
    return None


def dropped_pairs(
    states: dict[PairActivity, PairState],
    kinds: dict[PairActivity, str],
    dem_check: dict[str, bool],
    other_kind: str = OTHER_PAIR,
) -> tuple[DroppedPair, ...]:
    """The pairs dropped under engine_inputs Q1 (a), each with every end that drops it.

    Args:
        states: Every pair's state from `pair_states`.
        kinds: BOTH_ENDS_PAIR or GATEWAY_ONLY_PAIR for each selected pair.
        dem_check: Per node the run places, whether it passes the DEM edge check.
        other_kind: The kind of a pair not in kinds.

    Returns:
        A pair is dropped when either end straddles the tile edge, or an end the
        run places fails the DEM edge check; the other pairs are not listed.
    """
    dropped = []
    for pair, state in states.items():
        ends = zip(ENDS, (pair.relayer, pair.gateway), state, strict=True)
        reasons = tuple(
            (end, reason)
            for end, node, node_state in ends
            if (reason := _drop_reason(node, node_state, dem_check)) is not None
        )
        if reasons:
            dropped.append(DroppedPair(pair, kinds.get(pair, other_kind), reasons))
    return tuple(dropped)


def straddle_drops(links: CaptureLinks, tile: Tile, expect_pairs: int) -> tuple[DroppedPair, ...]:
    """The pairs a straddling node drops, found before the tile check can refuse.

    Casey, PR #51, (a): the drop lines print before the count checks, so a
    refusal shows which pair and which end caused it. The DEM edge check needs
    a placed box, so its drops come later, from `select_nodes`.

    Args:
        links: The capture's pairs and node positions.
        tile: The engine run's tile.
        expect_pairs: The union of pairs the tile check counted, 8 on 2026-11-08.

    Returns:
        Each pair with a straddling end, with its kind when every check of
        `select_nodes` before placement would pass (the pair count and the
        2026-11-08 tile counts), else KIND_NOT_DETERMINED.

    Raises:
        SelectionError: if a node has no usable position.
    """
    states = pair_states(links, tile)
    kinds = _tile_check(links, states, tile, expect_pairs)
    if isinstance(kinds, str):
        return dropped_pairs(states, {}, {}, other_kind=KIND_NOT_DETERMINED)
    return dropped_pairs(states, kinds, {})


def _place(nodes: dict[str, NodePositions], pseudonym: str, role: str) -> PlacedNode:
    found = nodes[pseudonym]
    box = found.stationary_box
    if box is None:
        raise SelectionError(
            f"the {role} sent {len(found.boxes)} boxes during the capture; "
            "the layers need its one stationary box"
        )
    easting_m, northing_m = _project(box.latitude_i, box.longitude_i)
    half_width_m = box_half_width_m(box.precision_bits, box.latitude_i / 1e7)
    return PlacedNode(pseudonym, role, easting_m, northing_m, half_width_m, found.precise_source)


def _tile_check(
    links: CaptureLinks, states: dict[PairActivity, PairState], tile: Tile, expect_pairs: int
) -> dict[PairActivity, str] | str:
    """The pairs touching the tile by kind, or the reason the 2026-11-08 counts refuse.

    The both-ends pair comes first, then the gateway-only pairs in the
    capture's pair order. The reason is returned, not raised, so
    `straddle_drops` can ask without catching anything; `select_nodes`
    raises it.
    """
    if len(links.pairs) != expect_pairs:
        return f"{len(links.pairs)} pairs, not the {expect_pairs} expected"
    counts = {pair: _counted(state) for pair, state in states.items()}
    both = [pair for pair, state in counts.items() if state == (IN, IN)]
    if len(both) != 1:
        return f"{len(both)} pairs have both ends in {tile.name}, not one"
    gateway_only = [pair for pair, state in counts.items() if state == (OUT, IN)]
    if len(gateway_only) != GATEWAY_ONLY_PAIRS:
        return f"{len(gateway_only)} pairs have only the gateway end in {tile.name}, not two"
    if tile_counts(states)["touched"] != PAIRS_TOUCHED:
        return f"pairs touching {tile.name} are not {PAIRS_TOUCHED}"
    return {both[0]: BOTH_ENDS_PAIR, **dict.fromkeys(gateway_only, GATEWAY_ONLY_PAIR)}


def _straddling(states: dict[PairActivity, PairState]) -> set[str]:
    """The nodes whose usable boxes lie on both sides of the tile edge."""
    return {
        node
        for pair, state in states.items()
        for node, end_state in zip((pair.relayer, pair.gateway), state, strict=True)
        if end_state == SPLIT
    }


def _roles(kinds: dict[PairActivity, str]) -> list[tuple[str, str]]:
    """(pseudonym, role) per selected node: each gateway once, both-ends first, then receiver."""
    sites: dict[str, str] = {}
    for pair, kind in kinds.items():
        sites.setdefault(
            pair.gateway, BOTH_ENDS_GATEWAY if kind == BOTH_ENDS_PAIR else GATEWAY_ONLY_GATEWAY
        )
    receiver = next(pair.relayer for pair, kind in kinds.items() if kind == BOTH_ENDS_PAIR)
    return [*sites.items(), (receiver, BOTH_ENDS_RELAYER)]


def select_nodes(links: CaptureLinks, tile: Tile, expect_pairs: int, extent: Extent) -> Selection:
    """The tile's real sites and receiver by tile membership, and the pairs dropped.

    Args:
        links: The capture's pairs and node positions.
        tile: The engine run's tile.
        expect_pairs: The union of pairs the tile check counted, 8 on 2026-11-08.
        extent: The DEM's edges in EPSG:26915 metres, from `dem_extent`.

    Returns:
        The placed nodes (the both-ends pair's gateway, then the gateway-only
        pairs' gateways in the capture's pair order, each gateway once, with
        the both-ends role first, then the both-ends pair's relayer), less any
        that straddle the tile edge, with their DEM edge check and the dropped
        pairs. No busiest-pair ranking enters the choice (SITE_RULE).
        `Selection.layers` refuses when a selected pair was dropped.

    Raises:
        SelectionError: when the pairs do not reproduce the 2026-11-08 tile
            counts, or a selected node that does not straddle sent more than
            one box during the capture.
    """
    # The pair count refuses before pair_states, so a call with a wrong count
    # and a node with no usable position names the count. The command calls
    # pair_states earlier, for its tile line, and refuses on the position.
    if len(links.pairs) != expect_pairs:
        raise SelectionError(f"{len(links.pairs)} pairs, not the {expect_pairs} expected")
    states = pair_states(links, tile)
    kinds = _tile_check(links, states, tile, expect_pairs)
    if isinstance(kinds, str):
        raise SelectionError(kinds)
    straddling = _straddling(states)
    placed = tuple(
        _place(links.nodes, node, role) for node, role in _roles(kinds) if node not in straddling
    )
    dem_check = {
        node.pseudonym: inside_dem((node.easting_m, node.northing_m), node.half_width_m, extent)
        for node in placed
    }
    return Selection(placed, dem_check, dropped_pairs(states, kinds, dem_check))
