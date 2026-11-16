"""The crossing check: run paths that pass near a surface cell far above the ground.

A tall cell is a DSM cell whose height exceeds the DEM's at the same place
by more than a threshold, 60 m for the x72y429 run (Casey, issue #44, Q2
(a)). The DSM is the first-return surface and the DEM the bare ground, so the
difference is the height of whatever the lidar hit there. The DEM is brought
onto each DSM tile's grid by nearest neighbour, as the engine's terrain build
does, so the two heights compared belong to the same square metre. DSM and
DEM are read window by window, never whole.

A path is the straight line between a site's and a receiver's box centres,
as `predictions.parquet` measures its distance (`docs/INTERFACE.md`). Its
distance to the tall cells is to their squares, not their centres; it
crosses when that distance is at most 10 m. This is a report for Casey, not
a rule the engine applies: the clutter rule of ADR 0006 is unchanged.
"""

import time
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
import rasterio
import shapely
import structlog

from engine.grid import RunGrid
from engine.points import Endpoint
from engine.rasters import read_cells, windows
from engine.snapshots import NamedSnapshot
from engine.terrain_build import _check_projected, _first_valid, _open_source, _warped

log = structlog.get_logger(component="engine.prepare.crossing")

TALL_CELL_M = 60.0
"""DSM minus DEM above which a cell is tall, metres."""
CROSSING_DISTANCE_M = 10.0
"""A path within this distance of a tall cell crosses it, metres."""

CellBounds = npt.NDArray[np.float64]
"""One row per cell: west, south, east, north in EPSG:26915 metres."""


@dataclass(frozen=True, kw_only=True)
class PathCrossing:
    """How close one site to receiver path passes to the tall cells.

    Attributes:
        site_id: The site.
        rx_id: The receiver.
        distance_m: Shortest distance in metres from the path to a tall
            cell's square; None when there is no tall cell.
        crosses: Whether that distance is at most the crossing distance.
    """

    site_id: str
    rx_id: str
    distance_m: float | None
    crosses: bool


def tall_cells(
    dem: NamedSnapshot, dsm: NamedSnapshot, threshold_m: float = TALL_CELL_M
) -> CellBounds:
    """The DSM cells more than `threshold_m` above the DEM, as squares.

    Args:
        dem: The verified DEM snapshot.
        dsm: The verified DSM snapshot.
        threshold_m: Height above the DEM in metres that a cell must exceed.

    Returns:
        An (n, 4) array of cell bounds in EPSG:26915, sorted; a cell without
        a DSM or DEM value is never tall.

    Raises:
        ConfigError: for a tile that does not open or is not north-up in
            EPSG:26915.
    """
    started = time.monotonic()
    found = [_tile_tall_cells(dem, path, dsm.snapshot_id, threshold_m) for path in dsm.rasters()]
    cells = np.unique(np.vstack([np.empty((0, 4)), *found]), axis=0)
    log.info(
        "tall_cells_found",
        dem=dem.snapshot_id,
        dsm=dsm.snapshot_id,
        threshold_m=threshold_m,
        cells=len(cells),
        duration_s=round(time.monotonic() - started, 3),
    )
    return cells


def _tile_tall_cells(
    dem: NamedSnapshot, dsm_path: Path, dsm_id: str, threshold_m: float
) -> CellBounds:
    with ExitStack() as stack:
        surface = stack.enter_context(_open_source(dsm_path, dsm_id))
        _check_projected(surface, dsm_id)
        grid = _tile_grid(surface)
        sources = [stack.enter_context(rasterio.open(p)) for p in dem.rasters()]
        ground = _first_valid([stack.enter_context(_warped(s, grid)) for s in sources])
        rows: list[npt.NDArray[np.intp]] = []
        cols: list[npt.NDArray[np.intp]] = []
        for window in windows(grid.height, grid.width):
            above = read_cells(surface, window) - ground(window)
            hit_rows, hit_cols = np.nonzero(np.nan_to_num(above, nan=-np.inf) > threshold_m)
            rows.append(hit_rows + int(window.row_off))
            cols.append(hit_cols + int(window.col_off))
    return _bounds(grid, np.concatenate(rows), np.concatenate(cols))


def _tile_grid(tile: rasterio.io.DatasetReader) -> RunGrid:
    bounds = tile.bounds
    return RunGrid(
        west_m=bounds.left,
        south_m=bounds.bottom,
        east_m=bounds.right,
        north_m=bounds.top,
        resolution_m=float(tile.transform.a),
    )


def _bounds(grid: RunGrid, rows: npt.NDArray[np.intp], cols: npt.NDArray[np.intp]) -> CellBounds:
    size = grid.resolution_m
    west = grid.west_m + cols * size
    north = grid.north_m - rows * size
    return np.column_stack([west, north - size, west + size, north]).astype(np.float64)


def path_crossings(
    sites: Sequence[Endpoint],
    receivers: Sequence[Endpoint],
    cells: CellBounds,
    within_m: float = CROSSING_DISTANCE_M,
) -> list[PathCrossing]:
    """Every site to receiver path's shortest distance to the tall cells.

    Args:
        sites: Sites, EPSG:26915; their box centres are the path ends.
        receivers: Receivers, EPSG:26915; the same.
        cells: Tall cell bounds from `tall_cells`.
        within_m: Distance in metres at or under which a path crosses.

    Returns:
        One entry per (site, receiver), site by site in input order.
    """
    pairs = [(site, rx) for site in sites for rx in receivers]
    if len(cells) == 0:
        return [
            PathCrossing(site_id=s.endpoint_id, rx_id=r.endpoint_id, distance_m=None, crosses=False)
            for s, r in pairs
        ]
    squares = shapely.union_all(shapely.box(cells[:, 0], cells[:, 1], cells[:, 2], cells[:, 3]))
    distances = shapely.distance([_path(site, rx) for site, rx in pairs], squares)
    return [
        PathCrossing(
            site_id=site.endpoint_id,
            rx_id=rx.endpoint_id,
            distance_m=float(distance),
            crosses=bool(distance <= within_m),
        )
        for (site, rx), distance in zip(pairs, distances, strict=True)
    ]


def _path(site: Endpoint, rx: Endpoint) -> shapely.Geometry:
    """The straight line between the box centres, or the one point when they coincide."""
    start, end = site.box.centre_xy_m, rx.box.centre_xy_m
    if start == end:
        return shapely.Point(start)
    return shapely.LineString([start, end])
