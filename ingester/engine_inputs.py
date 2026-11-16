"""Real site and receiver layers for an engine run on one USGS 1 m tile, from a capture snapshot.

`python -m ingester.engine_inputs` reads a finished capture snapshot (checked
against its manifest), finds the pairs positioned at both ends by the ADR 0003
spike's rules (`ingester.link_pairs`), and selects for the tile, as PLAN Phase
3 and docs/measurements-2026-11-08.md describe the test tile: the one pair with
both ends in the tile, and the gateway ends of the two busiest pairs. It writes
two GeoParquet layers in EPSG:26915 with the columns the lead agreed for the
x72y429 run (issue #44): `tx_sites_real.parquet` (`site_id`, `geometry`,
`box_half_width_m`, `role`) and `rx_real.parquet` (`rx_id`, `geometry`,
`box_half_width_m`). Geometry is the node's stationary box centre.

The selection is refused, and nothing is written, when the snapshot does not
reproduce the 2026-11-08 tile check: a pair count other than the one expected,
not exactly one pair with both ends in the tile, a busy pair whose gateway end
is not in the tile, or other than three pairs touching it. It is also refused
when the busiest two pairs depend on how busyness is measured, since the
measurements file does not name its measure, and when a selected node sent
more than one box during the capture. A refusal exits 2.

Per selected node it prints whether the box plus the engine's 50 m margin lies
inside the DEM extent, with the engine's own rule: `engine.grid.run_grid` is
imported and the comparison of `engine.terrain_build._check_covers` mirrored,
since that function reads the bounds from a DEM snapshot.

The layers hold real positions, precise MeshSTL ones among them: they and
anything built from them are never published (`pos_04`, Casey on issue #44).
The command refuses an output folder inside a git checkout and never prints or
logs a coordinate; its output gives pseudonyms, roles, counts, box half-widths
and pass or fail. It does not write the grid receivers, the run config or any
check of the paths; those are terrain-rf-eng's.
"""

import argparse
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import structlog
from pyproj import Transformer

from engine.grid import run_grid
from engine.points import Endpoint
from ingester.link_pairs import (
    USABLE_BITS,
    CaptureLinks,
    NodePositions,
    PairActivity,
    capture_links,
    read_capture_rows,
    spike_box_half_width_m,
)
from ingester.settings import get_ingester_settings
from pipeline.common.logging import configure_logging
from pipeline.common.snapshot import SnapshotError, snapshot_folder
from terrain.draws import EndpointBox

log = structlog.get_logger(component="ingester.engine_inputs")

LAYER_EPSG = 26915
SITES_FILE = "tx_sites_real.parquet"
RECEIVERS_FILE = "rx_real.parquet"
BOTH_ENDS_GATEWAY = "both_ends_gateway"
BUSY_PAIR_GATEWAY = "busy_pair_gateway"
BOTH_ENDS_RELAYER = "both_ends_relayer"
TILE_SIZE_M = 10_000.0
TILE_PATTERN = re.compile(r"x(\d+)y(\d+)")
# The tile check of 2026-11-08: one pair with both ends in the tile plus the
# gateway ends of the two busiest pairs, three pairs touched.
PAIRS_TOUCHED = 3
# The run grid is built at the DEM's pixel size; the 1 m DEM of the test tile.
DEM_RESOLUTION_M = 1.0
NEVER_PUBLISH = (
    "These layers hold real node positions, precise MeshSTL positions among them. They, and "
    "every coverage output built from them, are never published (pos_04; Casey, issue #44)."
)
_TO_METRES = Transformer.from_crs(4326, LAYER_EPSG, always_xy=True)

Extent = tuple[float, float, float, float]
"""West, south, east, north in EPSG:26915 metres."""


class SelectionError(Exception):
    """The selection is refused: the snapshot or the output folder does not allow it."""


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
    """A selected node at its stationary box centre in EPSG:26915."""

    pseudonym: str
    role: str
    easting_m: float
    northing_m: float
    half_width_m: float
    precise_source: bool


def parse_tile(name: str) -> Tile:
    """The tile named in USGS 1 m naming, such as x72y429.

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


def inside_dem(centre_xy_m: tuple[float, float], half_width_m: float, extent: Extent) -> bool:
    """Whether the engine's run grid for one box lies inside the DEM extent.

    Args:
        centre_xy_m: Box centre in EPSG:26915 metres.
        half_width_m: Box half-width in metres.
        extent: The DEM's west, south, east and north edges in EPSG:26915 metres.
    """
    box = EndpointBox(centre_xy_m=centre_xy_m, half_width_m=half_width_m)
    grid = run_grid([Endpoint(endpoint_id="check", box=box)], resolution_m=DEM_RESOLUTION_M)
    west, south, east, north = extent
    # engine.terrain_build._check_covers refuses a run when this holds.
    outside = grid.west_m < west or grid.south_m < south
    return not (outside or grid.east_m > east or grid.north_m > north)


def _project(latitude_i: int, longitude_i: int) -> tuple[float, float]:
    easting_m, northing_m = _TO_METRES.transform(longitude_i / 1e7, latitude_i / 1e7)
    return float(easting_m), float(northing_m)


def _tile_state(nodes: dict[str, NodePositions], pseudonym: str, tile: Tile) -> str:
    """`in`, `out` or `split` for where a node's usable positions lie against the tile."""
    found = nodes.get(pseudonym)
    usable = [] if found is None else [b for b in found.boxes if b.precision_bits >= USABLE_BITS]
    inside = {tile.contains(*_project(box.latitude_i, box.longitude_i)) for box in usable}
    if not inside:
        raise SelectionError(f"node {pseudonym} of a pair has no usable position")
    if len(inside) == 2:
        return "split"
    return "in" if inside == {True} else "out"


def _placed(nodes: dict[str, NodePositions], pseudonym: str, role: str) -> PlacedNode:
    found = nodes[pseudonym]
    box = found.stationary_box
    if box is None:
        raise SelectionError(
            f"{role} {pseudonym} sent {len(found.boxes)} boxes during the capture; "
            "the layers need its one stationary box"
        )
    easting_m, northing_m = _project(box.latitude_i, box.longitude_i)
    half_width_m = spike_box_half_width_m(box.precision_bits, box.latitude_i / 1e7)
    return PlacedNode(pseudonym, role, easting_m, northing_m, half_width_m, found.precise_source)


def _tile_pairs(links: CaptureLinks, tile: Tile) -> dict[PairActivity, tuple[str, str]]:
    states = {
        pair: (
            _tile_state(links.nodes, pair.relayer, tile),
            _tile_state(links.nodes, pair.gateway, tile),
        )
        for pair in links.pairs
    }
    split = [state for state in states.values() if "split" in state]
    if split:
        raise SelectionError(
            f"{len(split)} pairs have a node with positions on both sides of the tile edge"
        )
    return states


def _print_tile(tile: Tile, states: dict[PairActivity, tuple[str, str]]) -> None:
    counts = {
        "both ends": sum(1 for state in states.values() if state == ("in", "in")),
        "only the gateway end": sum(1 for state in states.values() if state == ("out", "in")),
        "only the relayer end": sum(1 for state in states.values() if state == ("in", "out")),
    }
    touched = sum(1 for state in states.values() if "in" in state)
    parts = ", ".join(f"{label} {count}" for label, count in counts.items())
    print(f"tile {tile.name}: pairs with {parts}; pairs touched {touched}")


def select_nodes(
    links: CaptureLinks, tile: Tile, expect_pairs: int
) -> tuple[list[PlacedNode], PlacedNode]:
    """The tile's real sites and the both-ends pair's relayer, as the 2026-11-08 check found them.

    Returns:
        The sites (the both-ends pair's gateway, then the busy pairs' gateways,
        each gateway once) and the receiver.

    Raises:
        SelectionError: when the snapshot does not reproduce the tile check,
            the busiest pairs depend on the measure, or a node moved.
    """
    if len(links.pairs) != expect_pairs:
        raise SelectionError(f"{len(links.pairs)} pairs, not the {expect_pairs} expected")
    states = _tile_pairs(links, tile)
    _print_tile(tile, states)
    both = [pair for pair, state in states.items() if state == ("in", "in")]
    if len(both) != 1:
        raise SelectionError(f"{len(both)} pairs have both ends in {tile.name}, not one")
    busy = busiest_two(links.pairs)
    if both[0] in busy or any(states[pair] != ("out", "in") for pair in busy):
        raise SelectionError(f"the two busiest pairs are not gateway-only pairs of {tile.name}")
    if sum(1 for state in states.values() if "in" in state) != PAIRS_TOUCHED:
        raise SelectionError(f"pairs touching {tile.name} are not {PAIRS_TOUCHED}")
    roles = {both[0].gateway: BOTH_ENDS_GATEWAY}
    for pair in busy:
        roles.setdefault(pair.gateway, BUSY_PAIR_GATEWAY)
    sites = [_placed(links.nodes, node, role) for node, role in roles.items()]
    return sites, _placed(links.nodes, both[0].relayer, BOTH_ENDS_RELAYER)


def _print_pairs(links: CaptureLinks) -> None:
    counts = links.pair_counts
    missing = links.one_coordinate_missing
    print(f"positions with one coordinate missing, not positions (pos_01): {missing}")
    print(
        "pairs positioned at both ends, stationarity rule, 500 m: "
        f"public {counts['public']}, MeshSTL {counts['meshstl']}, "
        f"shared {counts['shared']}, union {counts['union']}"
    )
    for pair in links.pairs:
        print(
            f"pair {pair.relayer} to {pair.gateway}: link-hours over both runs "
            f"{pair.link_hours_union}, public {pair.link_hours_public}, MeshSTL "
            f"{pair.link_hours_meshstl}; receipts {pair.receipts}"
        )


def _print_selection(sites: list[PlacedNode], receiver: PlacedNode, extent: Extent) -> None:
    print(f"busiest two pairs, the same under every measure: {', '.join(MEASURES)}")
    for node in [*sites, receiver]:
        verdict = (
            "pass"
            if inside_dem((node.easting_m, node.northing_m), node.half_width_m, extent)
            else "fail"
        )
        print(
            f"{node.pseudonym} {node.role}: box half-width {node.half_width_m:.1f} m, "
            f"precise source {str(node.precise_source).lower()}, box plus 50 m inside the DEM "
            f"(engine rule): extent {verdict}"
        )


def _inside_checkout(folder: Path) -> bool:
    resolved = folder.resolve()
    return any((parent / ".git").exists() for parent in (resolved, *resolved.parents))


def _check_targets(out: Path) -> list[Path]:
    if _inside_checkout(out):
        raise SelectionError(f"{out} is inside a git checkout; real positions stay out of git")
    targets = [out / SITES_FILE, out / RECEIVERS_FILE]
    existing = [path.name for path in targets if path.exists()]
    if existing:
        raise SelectionError(f"{', '.join(existing)} exists in {out}; nothing is overwritten")
    return targets


def _frame(nodes: list[PlacedNode], id_column: str, with_role: bool) -> gpd.GeoDataFrame:
    columns: dict[str, list[object]] = {id_column: [node.pseudonym for node in nodes]}
    geometry = gpd.points_from_xy(
        [node.easting_m for node in nodes], [node.northing_m for node in nodes]
    )
    columns["box_half_width_m"] = [float(node.half_width_m) for node in nodes]
    if with_role:
        columns["role"] = [node.role for node in nodes]
    frame = gpd.GeoDataFrame(columns, geometry=geometry, crs=f"EPSG:{LAYER_EPSG}")
    order = [id_column, "geometry", "box_half_width_m", *(["role"] if with_role else [])]
    return frame[order]


def _write(path: Path, frame: gpd.GeoDataFrame) -> None:
    started = time.monotonic()
    # "x" creates the file and fails if it exists, so nothing is ever overwritten.
    with path.open("xb") as handle:
        frame.to_parquet(handle)
    duration_s = round(time.monotonic() - started, 3)
    log.info(
        "layer_written",
        path=str(path),
        rows=len(frame),
        bytes=path.stat().st_size,
        duration_s=duration_s,
    )


def write_layers(out: Path, sites: list[PlacedNode], receiver: PlacedNode) -> None:
    """Write the two layers into out, creating it, refusing any file that exists.

    Raises:
        SelectionError: if out is inside a git checkout or a layer exists.
    """
    sites_path, receivers_path = _check_targets(out)
    out.mkdir(parents=True, exist_ok=True)
    _write(sites_path, _frame(sites, "site_id", with_role=True))
    _write(receivers_path, _frame([receiver], "rx_id", with_role=False))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--snapshot", required=True, help="the capture snapshot id")
    parser.add_argument("--tile", required=True, help="USGS 1 m tile name, such as x72y429")
    parser.add_argument("--out", required=True, type=Path, help="folder for the two layers")
    parser.add_argument(
        "--dem-extent", required=True, nargs=4, type=float,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
        help="the DEM snapshot's bounds in EPSG:26915 metres",
    )  # fmt: skip
    parser.add_argument(
        "--expect-pairs", required=True, type=int,
        help="the union of both-ends pairs the tile check found (8 on 2026-11-08)",
    )  # fmt: skip
    parser.add_argument("--snapshots-dir", type=Path, default=None)
    return parser


def _run(args: argparse.Namespace) -> None:
    tile = parse_tile(args.tile)
    _check_targets(args.out)
    root = args.snapshots_dir or get_ingester_settings().snapshots_dir
    rows = read_capture_rows(snapshot_folder(root, args.snapshot))
    print(f"snapshot {args.snapshot}: {len(rows)} delivery rows, every file matched its manifest")
    links = capture_links(rows)
    _print_pairs(links)
    sites, receiver = select_nodes(links, tile, args.expect_pairs)
    _print_selection(sites, receiver, tuple(args.dem_extent))
    write_layers(args.out, sites, receiver)
    print(f"wrote {SITES_FILE} ({len(sites)} sites) and {RECEIVERS_FILE} (1 receiver)")
    print(NEVER_PUBLISH)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command; 0 when both layers were written, 2 when the selection was refused."""
    args = _parser().parse_args(argv)
    try:
        _run(args)
    except (SelectionError, SnapshotError) as err:
        log.error("engine_inputs_refused", reason=str(err))
        print(f"refused, nothing written: {err}")
        return 2
    return 0


if __name__ == "__main__":
    configure_logging("console")
    sys.exit(main())
