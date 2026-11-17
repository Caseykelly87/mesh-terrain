"""The terrain build: the run's DEM, DSM, canopy and clutter as COGs on the run grid.

Each layer is a mosaic of the named snapshot's GeoTIFFs over the run grid,
in EPSG:26915:

- `dem.tif` and `dsm.tif` copy their source pixels: the sources must already
  be in EPSG:26915 on the grid's lattice, so nearest-neighbour warping picks
  exactly one source pixel per cell and changes no height.
- `canopy.tif` is warped from the canopy tiles (EPSG:3857 for the Meta and WRI
  maps) by nearest neighbour onto the 1 m DEM's grid (Casey's B1), so each
  1 m cell takes the one canopy pixel under its centre and no tree is smeared.
- `clutter.tif` is the per-pixel rule of `terrain.clutter.clutter_height_m`
  (ADR 0006 decision 6, items 6 (a) and 11 (i)) on those three layers; a
  canopy cell without a value counts as 0 there and is counted for the run
  report, per canopy tile.

Where tiles overlap, the first tile by path that has a value wins. Every read
is a window; no source or layer is read whole. This module checks the sources
before anything is written, so a run that cannot be built is refused first.
"""

import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
import structlog
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError, WindowError
from rasterio.io import DatasetReader
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds

from engine.errors import ConfigError
from engine.grid import RunGrid
from engine.rasters import FloatArray, read_cells, windows, write_cog
from engine.snapshots import NamedSnapshot
from terrain.clutter import clutter_height_m

log = structlog.get_logger(component="engine.terrain_build")

LAYER_CRS_EPSG = 26915
FINE_RESOLUTION_M = 1.0
MOSAIC_ROLES = ("dem", "dsm", "canopy")
_FILL = {"uint8": 255, "int16": -32768, "float32": -9999.0, "float64": -9999.0}
NO_NODATA = "no nodata value declared"
OUTSIDE = "outside the run extent"


@dataclass(frozen=True, kw_only=True)
class TerrainBuild:
    """The run's terrain layers and what the run report says about them.

    Attributes:
        dem: `terrain/dem.tif`.
        dsm: `terrain/dsm.tif`, or None without a DSM snapshot.
        canopy: `terrain/canopy.tif`, or None without a canopy snapshot.
        clutter: `terrain/clutter.tif`, or None without either.
        resolution_m: The DEM's pixel size in metres.
        canopy_nodata_by_tile: Canopy tile file name to its pixels without a
            value inside the run extent's bounding box, in the tile's own
            pixels, or `NO_NODATA` for a tile that declares no nodata value,
            or `OUTSIDE` for a tile that does not reach the run extent.
        canopy_nodata_cells: Cells of `canopy.tif` without a value, counted as
            canopy 0 in clutter; None without a canopy snapshot.
    """

    dem: Path
    dsm: Path | None
    canopy: Path | None
    clutter: Path | None
    resolution_m: float
    canopy_nodata_by_tile: dict[str, int | str]
    canopy_nodata_cells: int | None


def dem_resolution_m(dem: NamedSnapshot) -> float:
    """The pixel size shared by every DEM tile, after checking the tiles' grids.

    Raises:
        ConfigError: for a tile that is not north-up in EPSG:26915 with
            square pixels, or tiles of different pixel sizes.
    """
    sizes = set()
    for path in dem.rasters():
        with open_source(path, dem.snapshot_id) as tile:
            check_projected(tile, dem.snapshot_id)
            sizes.add(float(tile.transform.a))
    if len(sizes) != 1:
        raise ConfigError(f"snapshot {dem.snapshot_id} mixes pixel sizes {sorted(sizes)}")
    return sizes.pop()


def check_sources(snapshots: dict[str, NamedSnapshot], grid: RunGrid) -> None:
    """Refuse a build that would leave the DEM or misplace a pixel, before anything is written.

    Raises:
        ConfigError: when the grid reaches beyond the DEM tiles' extent, a
            DSM or canopy comes with a DEM other than 1 m, or a DEM or DSM tile
            is off the grid's lattice.
    """
    dem = snapshots["dem"]
    check_covers(dem, grid)
    for role in ("dsm", "canopy"):
        if role in snapshots and grid.resolution_m != FINE_RESOLUTION_M:
            raise ConfigError(f"snapshots.{role} needs a 1 m DEM; {dem.snapshot_id} is not")
    for role in ("dem", "dsm"):
        if role in snapshots:
            for path in snapshots[role].rasters():
                _check_lattice(path, snapshots[role].snapshot_id, grid)


def build_terrain(snapshots: dict[str, NamedSnapshot], grid: RunGrid, folder: Path) -> TerrainBuild:
    """Write the terrain layers of a run into `folder`.

    Args:
        snapshots: Verified snapshots by role; `dem` always.
        grid: The run grid at the DEM's resolution.
        folder: The run's `terrain/` folder, created here.

    Returns:
        The written layers and the canopy gap counts.
    """
    folder.mkdir()
    layers = {role: folder / f"{role}.tif" for role in MOSAIC_ROLES if role in snapshots}
    for role, path in layers.items():
        _mosaic_layer(snapshots[role], grid, path)
    clutter = None
    if "dsm" in layers or "canopy" in layers:
        clutter = folder / "clutter.tif"
        _clutter_layer(layers, grid, clutter)
    canopy = snapshots.get("canopy")
    return TerrainBuild(
        dem=layers["dem"],
        dsm=layers.get("dsm"),
        canopy=layers.get("canopy"),
        clutter=clutter,
        resolution_m=grid.resolution_m,
        canopy_nodata_by_tile={} if canopy is None else canopy_nodata_by_tile(canopy, grid),
        canopy_nodata_cells=None if "canopy" not in layers else count_nodata(layers["canopy"]),
    )


def open_source(path: Path, snapshot_id: str) -> DatasetReader:
    """Open a snapshot raster, refusing one GDAL cannot read before anything is written."""
    try:
        return rasterio.open(path)
    except RasterioIOError as err:
        raise ConfigError(f"snapshot {snapshot_id}: {path.name} does not open: {err}") from err


def check_projected(tile: DatasetReader, snapshot_id: str) -> None:
    """Refuse a tile that is not north-up in EPSG:26915 with square pixels.

    Raises:
        ConfigError: naming the tile.
    """
    transform = tile.transform
    if tile.crs is None or tile.crs.to_epsg() != LAYER_CRS_EPSG:
        raise ConfigError(
            f"snapshot {snapshot_id}: {Path(tile.name).name} is in {tile.crs}, not EPSG:26915; "
            "the metro DEM's projection is an open question (docs/STATUS.md)"
        )
    if transform.b != 0.0 or transform.d != 0.0 or transform.a != -transform.e:
        raise ConfigError(
            f"snapshot {snapshot_id}: {Path(tile.name).name} is not north-up and square"
        )


def dem_extent(dem: NamedSnapshot) -> tuple[float, float, float, float]:
    """The bounding box of the DEM tiles: west, south, east, north in EPSG:26915 metres.

    Raises:
        ConfigError: for a tile that does not open.
    """
    bounds = []
    for path in dem.rasters():
        with open_source(path, dem.snapshot_id) as tile:
            bounds.append(tile.bounds)
    return (
        min(b.left for b in bounds),
        min(b.bottom for b in bounds),
        max(b.right for b in bounds),
        max(b.top for b in bounds),
    )


def grid_inside(grid: RunGrid, extent: tuple[float, float, float, float]) -> bool:
    """Whether the grid lies inside the extent (west, south, east, north), edges included."""
    west, south, east, north = extent
    return (
        grid.west_m >= west
        and grid.south_m >= south
        and grid.east_m <= east
        and grid.north_m <= north
    )


def check_covers(dem: NamedSnapshot, grid: RunGrid) -> None:
    """Refuse a grid that reaches beyond the DEM tiles' bounding box.

    Raises:
        ConfigError: naming the run extent and the DEM's, in metres.
    """
    west, south, east, north = dem_extent(dem)
    if not grid_inside(grid, (west, south, east, north)):
        raise ConfigError(
            f"the run extent ({grid.west_m:.0f}, {grid.south_m:.0f}) to ({grid.east_m:.0f}, "
            f"{grid.north_m:.0f}) with its {grid.resolution_m:g} m grid reaches outside the DEM "
            f"snapshot {dem.snapshot_id} ({west:.0f}, {south:.0f}) to ({east:.0f}, {north:.0f})"
        )


def _check_lattice(path: Path, snapshot_id: str, grid: RunGrid) -> None:
    with open_source(path, snapshot_id) as tile:
        check_projected(tile, snapshot_id)
        transform = tile.transform
    offsets = (
        (transform.c - grid.west_m) / grid.resolution_m,
        (grid.north_m - transform.f) / grid.resolution_m,
    )
    if transform.a != grid.resolution_m or not all(float(o).is_integer() for o in offsets):
        raise ConfigError(f"snapshot {snapshot_id}: {path.name} is off the run grid's lattice")


def warped_to_grid(source: DatasetReader, grid: RunGrid) -> WarpedVRT:
    """The source on the run grid by nearest neighbour: a copy on the lattice, B1 otherwise."""
    options = {}
    if source.nodata is None:
        # Safe to continue: cells outside the source still come out empty
        # through a stand-in nodata value; a source cell equal to it would be
        # read as empty, which is why it is logged.
        log.warning("source_without_nodata", path=str(source.name), fill=_FILL[source.dtypes[0]])
        options["nodata"] = _FILL[source.dtypes[0]]
    return WarpedVRT(
        source,
        crs=f"EPSG:{LAYER_CRS_EPSG}",
        transform=grid.transform,
        width=grid.width,
        height=grid.height,
        resampling=Resampling.nearest,
        **options,
    )


def _mosaic_layer(snapshot: NamedSnapshot, grid: RunGrid, path: Path) -> None:
    log.info("terrain_layer_begin", snapshot_id=snapshot.snapshot_id, path=str(path))
    started = time.monotonic()
    with ExitStack() as stack:
        sources = [stack.enter_context(rasterio.open(p)) for p in snapshot.rasters()]
        vrts = [stack.enter_context(warped_to_grid(s, grid)) for s in sources]
        write_cog(path, grid, first_valid(vrts))
    log.info(
        "terrain_layer_end",
        snapshot_id=snapshot.snapshot_id,
        path=str(path),
        duration_s=round(time.monotonic() - started, 3),
    )


def first_valid(vrts: list[WarpedVRT]) -> Callable[[Window], FloatArray]:
    """Cells of a window of the grid from the first source with a value, NaN where none has."""

    def cells(window: Window) -> FloatArray:
        mosaic = np.full((int(window.height), int(window.width)), np.nan)
        for vrt in vrts:
            block = read_cells(vrt, window)
            empty = np.isnan(mosaic)
            mosaic[empty] = block[empty]
        return mosaic

    return cells


def _clutter_layer(layers: dict[str, Path], grid: RunGrid, path: Path) -> None:
    with ExitStack() as stack:
        opened = {role: stack.enter_context(rasterio.open(p)) for role, p in layers.items()}

        def cells(window: Window) -> FloatArray:
            dem = read_cells(opened["dem"], window)
            dsm = read_cells(opened["dsm"], window) if "dsm" in opened else None
            canopy = read_cells(opened["canopy"], window) if "canopy" in opened else None
            return clutter_height_m(dem, dsm_m=dsm, canopy_m=canopy)

        write_cog(path, grid, cells)


def canopy_nodata_by_tile(canopy: NamedSnapshot, grid: RunGrid) -> dict[str, int | str]:
    """Pixels without a value in each canopy tile, inside the run extent's bounding box.

    Counted in the tile's own pixels and CRS, over the bounding box of the run
    grid transformed into it. A tile that declares no nodata value is
    `NO_NODATA`, and one that does not reach the run extent is `OUTSIDE`.
    """
    counts: dict[str, int | str] = {}
    for path in canopy.rasters():
        with rasterio.open(path) as tile:
            counts[path.name] = NO_NODATA if tile.nodata is None else _tile_gaps(tile, grid)
    return counts


def _tile_gaps(tile: DatasetReader, grid: RunGrid) -> int | str:
    bounds = transform_bounds(
        f"EPSG:{LAYER_CRS_EPSG}", tile.crs, grid.west_m, grid.south_m, grid.east_m, grid.north_m
    )
    window = from_bounds(*bounds, transform=tile.transform).round_offsets().round_lengths()
    try:
        window = window.intersection(Window(0, 0, tile.width, tile.height))
    except WindowError:
        return OUTSIDE
    gaps = 0
    for block in windows(int(window.height), int(window.width)):
        shifted = Window(
            block.col_off + window.col_off,
            block.row_off + window.row_off,
            block.width,
            block.height,
        )
        gaps += int(np.count_nonzero(tile.read(1, window=shifted) == tile.nodata))
    return gaps


def count_nodata(path: Path) -> int:
    """Cells of a raster without a value, read window by window."""
    with rasterio.open(path) as raster:
        return sum(
            int(np.count_nonzero(np.isnan(read_cells(raster, window))))
            for window in windows(raster.height, raster.width)
        )
