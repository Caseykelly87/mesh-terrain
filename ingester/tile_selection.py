"""Which real nodes an engine run on one USGS 1 m tile uses, and whether each fits the DEM.

The test tile is defined by three pairs (PLAN Phase 3,
docs/measurements-2026-11-08.md): the one pair with both ends in the tile,
whose gateway is a site and whose relayer is a receiver, and the two busiest
pairs, whose gateway ends in the tile are sites. `select_nodes` returns those
nodes at their stationary box centres in EPSG:26915, or refuses when the pairs
do not reproduce that check.

The measurements file does not say how it ranked the busiest pairs, so
`busiest_two` ranks by three measures and refuses unless all three pick the
same two pairs. The measures can only refuse a selection; they never change
which sites are chosen.

The DEM check uses the engine's rule: `engine.grid.run_grid` builds the grid
for one box plus the engine's 50 m margin, and the comparison of
`engine.terrain_build._check_covers` is mirrored on the DEM snapshot's bounds,
read through the engine's own snapshot reader. Nothing here prints, logs or
puts a coordinate in an error message.
"""

import re
from collections.abc import Callable, Sequence
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
BUSY_PAIR_GATEWAY = "busy_pair_gateway"
BOTH_ENDS_RELAYER = "both_ends_relayer"
TILE_SIZE_M = 10_000.0
TILE_PATTERN = re.compile(r"x(\d+)y(\d+)")
# The tile check of 2026-11-08: one pair with both ends in the tile plus the
# gateway ends of the two busiest pairs, three pairs touched.
PAIRS_TOUCHED = 3
# The grid's pixel size does not move its edges, which lie on 10 m multiples
# whatever the size; 1 m is the test tile DEM's.
DEM_RESOLUTION_M = 1.0
_TO_METRES = Transformer.from_crs(4326, LAYER_EPSG, always_xy=True)

Extent = tuple[float, float, float, float]
"""West, south, east, north in EPSG:26915 metres."""
PairState = tuple[str, str]
"""Where a pair's relayer and gateway lie against the tile: `in` or `out` each."""


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
        role: `both_ends_gateway`, `busy_pair_gateway` or `both_ends_relayer`.
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


MEASURES: dict[str, Callable[[PairActivity], int]] = {
    "link-hours over both runs": lambda pair: pair.link_hours_union,
    "link-hours, public plus MeshSTL": lambda pair: (
        pair.link_hours_public + pair.link_hours_meshstl
    ),
    "receipts in those link-hours": lambda pair: pair.receipts,
}


def busiest_two(pairs: Sequence[PairActivity]) -> tuple[PairActivity, PairActivity]:
    """The two busiest pairs, when every measure in MEASURES picks the same two without a tie.

    Args:
        pairs: Every pair of the capture.

    Returns:
        The two pairs, busiest first by the first measure.

    Raises:
        SelectionError: with fewer than two pairs, a tie for second place, or
            measures that pick different pairs.
    """
    if len(pairs) < 2:
        raise SelectionError(f"{len(pairs)} pairs; the selection needs two busy pairs")
    picks = []
    for name, measure in MEASURES.items():
        ranked = sorted(pairs, key=measure, reverse=True)
        if len(ranked) > 2 and measure(ranked[1]) == measure(ranked[2]):
            raise SelectionError(f"a tie for second busiest pair by {name}")
        picks.append(ranked[:2])
    if any(set(pick) != set(picks[0]) for pick in picks):
        raise SelectionError("the two busiest pairs depend on the measure of busyness")
    first, second = picks[0]
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
        False exactly when engine.terrain_build._check_covers would refuse a
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
        return "split"
    return "in" if inside == {True} else "out"


def pair_states(links: CaptureLinks, tile: Tile) -> dict[PairActivity, PairState]:
    """Where each pair's relayer and gateway lie against the tile.

    Raises:
        SelectionError: if a node has no usable position, or usable positions
            on both sides of the tile edge.
    """
    states = {
        pair: (
            _node_state(links.nodes, pair.relayer, tile),
            _node_state(links.nodes, pair.gateway, tile),
        )
        for pair in links.pairs
    }
    split = [state for state in states.values() if "split" in state]
    if split:
        raise SelectionError(
            f"{len(split)} pairs have a node with positions on both sides of the tile edge"
        )
    return states


def tile_counts(states: dict[PairActivity, PairState]) -> dict[str, int]:
    """Pairs with both ends, only the gateway end, only the relayer end in the tile, and touched."""
    values = list(states.values())
    return {
        "both ends": values.count(("in", "in")),
        "only the gateway end": values.count(("out", "in")),
        "only the relayer end": values.count(("in", "out")),
        "touched": sum(1 for state in values if "in" in state),
    }


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


def select_nodes(
    links: CaptureLinks, tile: Tile, expect_pairs: int
) -> tuple[list[PlacedNode], PlacedNode]:
    """The tile's real sites and the both-ends pair's relayer, as the 2026-11-08 check found them.

    Args:
        links: The capture's pairs and node positions.
        tile: The engine run's tile.
        expect_pairs: The union of pairs the tile check counted, 8 on 2026-11-08.

    Returns:
        The sites (the both-ends pair's gateway, then the busy pairs' gateways,
        each gateway once, with the both-ends role first) and the receiver.

    Raises:
        SelectionError: when the pairs do not reproduce the tile check, the
            busiest pairs depend on the measure, or a selected node sent more
            than one box during the capture.
    """
    if len(links.pairs) != expect_pairs:
        raise SelectionError(f"{len(links.pairs)} pairs, not the {expect_pairs} expected")
    states = pair_states(links, tile)
    both = [pair for pair, state in states.items() if state == ("in", "in")]
    if len(both) != 1:
        raise SelectionError(f"{len(both)} pairs have both ends in {tile.name}, not one")
    busy = busiest_two(links.pairs)
    if both[0] in busy or any(states[pair] != ("out", "in") for pair in busy):
        raise SelectionError(f"the two busiest pairs are not gateway-only pairs of {tile.name}")
    if tile_counts(states)["touched"] != PAIRS_TOUCHED:
        raise SelectionError(f"pairs touching {tile.name} are not {PAIRS_TOUCHED}")
    roles = {both[0].gateway: BOTH_ENDS_GATEWAY}
    for pair in busy:
        roles.setdefault(pair.gateway, BUSY_PAIR_GATEWAY)
    sites = [_place(links.nodes, node, role) for node, role in roles.items()]
    return sites, _place(links.nodes, both[0].relayer, BOTH_ENDS_RELAYER)
