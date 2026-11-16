"""P.1812's 10 m profile on the 1 m layers, read from fixed 10 m cells.

P.1812-8 section 3.2.2 warns that surface data spaced much under 10 m can
overstate the loss, because it describes single obstacles, and asks for surface
heights at about the profile spacing. So on the 1 m layers P.1812 gets its own
profile every 10 m (ADR 0006 decision 6 as amended by item 1 (b)), built as items
6 to 9 decide:

- Clutter is computed per 1 m pixel by `terrain.clutter` first (item 6 (a)).
- Ground and clutter are then averaged separately over fixed blocks of 10 by 10
  pixels on the 1 m grid of the run's DEM (item 8 (a)), with block edges on
  eastings and northings that are multiples of 10 m (the lead's choice on
  PR #33), so a link averages the same blocks whichever raster it is read from:
  a TNM tile, the engine's cropped `dem.tif` or a corridor. The 1 m grid must
  therefore sit on whole metres. A block holding any pixel without a value has
  no value, and a draw whose profile reads it is refused as `empty_cell` (item
  7 (a)). Cells are computed once per read window and never written out; they
  do not depend on where a window starts.
- The profile samples the cell means every 10 m with bilinear interpolation
  (`scipy.ndimage.map_coordinates`, order 1), as every other profile does.
- The two ends take their ground from the 1 m DEM at the endpoint (item 9 (a)),
  which the caller passes in from the 1 m profile, so every model puts an
  antenna at the same height. Their clutter is 0: P.1812 adds no clutter at the
  terminals (P.1812-8 section 3.2, equations (1d) and (1e)), so the profile
  reads no cell there.

Everything is in EPSG:26915 (UTM zone 15N, metres); heights are metres above
NAVD88, clutter metres above ground. This module does not open rasters, call
P.1812 or decide what a refusal does to a link.
"""

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import structlog
from rasterio.io import DatasetReader
from rasterio.windows import Window
from scipy.ndimage import map_coordinates

from terrain.clutter import BoolArray, clutter_height_m, dsm_filled_mask
from terrain.errors import RefusedDrawError
from terrain.profile import (
    XY,
    FloatArray,
    PathProfile,
    check_extent,
    check_grid,
    pixel_indices,
    points_along,
    profile_distances_m,
    window_slices,
)

CELL_PX = 10
"""Pixels on a side of one cell; with 1 m pixels a cell is 10 m square."""

CELL_SIZE_M = 10.0
"""Side of one cell in metres; cell edges lie on multiples of it in EPSG:26915."""

CELL_STEP_M = 10.0
"""Largest spacing of P.1812's profile on the 1 m layers, metres."""

_MARGIN_CELLS = 1

log = structlog.get_logger(component="terrain.cells")


@dataclass(frozen=True, kw_only=True)
class FineLayers:
    """The open 1 m layers of one run, all on the DEM's grid in EPSG:26915.

    Attributes:
        dem: Bare-earth DEM, north-up, 1 m pixels.
        dsm: First-return lidar surface on the same grid, or None.
        canopy: Canopy height above ground on the same grid, or None.
    """

    dem: DatasetReader
    dsm: DatasetReader | None = None
    canopy: DatasetReader | None = None

    def __post_init__(self) -> None:
        """Check the layers can give clutter and share one 1 m grid.

        Raises:
            ValueError: without a DSM or a canopy layer, when the DEM is not
                north-up in EPSG:26915 with 1 m pixels running west to east
                whose edges sit on whole metres, or when a layer's grid differs from the DEM's.
        """
        if self.dsm is None and self.canopy is None:
            raise ValueError("the 1 m layers need a DSM or a canopy layer for clutter")
        name = Path(self.dem.name).name
        check_grid(self.dem, name)
        if self.dem.transform.a != 1.0 or self.dem.transform.e != -1.0:
            raise ValueError(
                f"{name} must have 1 m pixels, columns west to east and rows north to south"
            )
        if not (
            float(self.dem.transform.c).is_integer() and float(self.dem.transform.f).is_integer()
        ):
            raise ValueError(f"{name} must sit on whole metres for cells on UTM multiples of 10 m")
        for layer in (self.dsm, self.canopy):
            if layer is not None and not _same_grid(layer, self.dem):
                raise ValueError(f"{Path(layer.name).name} is not on the grid of {name}")


@dataclass(frozen=True, eq=False)
class CellProfile:
    """P.1812's 10 m profile and the DSM gaps filled in the cells it read.

    Attributes:
        profile: Ground and clutter every 10 m, in EPSG:26915.
        dsm_filled_pixels: Pixels set to clutter 0 because the DSM had no value
            and the DEM had one, over the cells the profile read; None without
            a DSM layer.
    """

    profile: PathProfile
    dsm_filled_pixels: int | None


def cell_profile(
    layers: FineLayers,
    tx_xy_m: XY,
    rx_xy_m: XY,
    *,
    tx_ground_m: float,
    rx_ground_m: float,
    step_m: float = CELL_STEP_M,
    max_window_cells: int = 100,
) -> CellProfile:
    """Ground and clutter every 10 m along a path, from 10 m cell means.

    Args:
        layers: The run's open 1 m layers.
        tx_xy_m: Transmitter easting and northing in EPSG:26915, metres.
        rx_xy_m: Receiver easting and northing in EPSG:26915, metres.
        tx_ground_m: 1 m DEM height at the transmitter, metres above NAVD88.
        rx_ground_m: 1 m DEM height at the receiver, metres above NAVD88.
        step_m: Largest spacing in metres (`terrain.profile.profile_distances_m`).
        max_window_cells: Largest read window side in cells; 100 is 1 km.

    Returns:
        The profile with clutter, and the count of filled DSM pixels.

    Raises:
        ZeroLengthPathError: if the two endpoints coincide.
        OutOfExtentError: if the path leaves the DEM.
        RefusedDrawError: "dem_gap" when an end's ground is NaN, "empty_cell"
            when an interior point reads a cell without a value.
    """
    length_m = math.hypot(rx_xy_m[0] - tx_xy_m[0], rx_xy_m[1] - tx_xy_m[1])
    distance_m = profile_distances_m(length_m, step_m)
    x_m, y_m = points_along(tx_xy_m, rx_xy_m, distance_m / length_m)
    rows, cols = pixel_indices(layers.dem, x_m, y_m)
    check_extent(rows, cols, layers.dem.height, layers.dem.width, Path(layers.dem.name).name)
    interior = _logged_cell_read(
        layers, _cell_row(y_m[1:-1]), _cell_col(x_m[1:-1]), max_window_cells
    )
    ground_m = np.concatenate(([tx_ground_m], interior.ground_m, [rx_ground_m]))
    clutter_m = np.concatenate(([0.0], interior.clutter_m, [0.0]))
    _refuse_missing(ground_m, clutter_m)
    profile = PathProfile(
        distance_m=distance_m,
        ground_m=ground_m,
        clutter_m=clutter_m,
        tx_xy_m=tx_xy_m,
        rx_xy_m=rx_xy_m,
    )
    filled = None if layers.dsm is None else sum(interior.filled_by_cell.values())
    return CellProfile(profile=profile, dsm_filled_pixels=filled)


@dataclass(frozen=True, eq=False)
class _CellSamples:
    ground_m: FloatArray
    clutter_m: FloatArray
    filled_by_cell: dict[tuple[int, int], int]


def _same_grid(layer: DatasetReader, dem: DatasetReader) -> bool:
    return bool(
        layer.shape == dem.shape and layer.transform == dem.transform and layer.crs == dem.crs
    )


def _cell_col(x_m: FloatArray) -> FloatArray:
    """Fractional cell column from easting, an integer being a cell centre.

    Cell column j spans eastings 10 j to 10 j + 10 m, centred on 10 j + 5.
    """
    col: FloatArray = (x_m - CELL_SIZE_M / 2.0) / CELL_SIZE_M
    return col


def _cell_row(y_m: FloatArray) -> FloatArray:
    """Fractional cell row from northing, rows counting south like raster rows.

    Cell row i spans northings -10 i - 10 to -10 i m, centred on -10 i - 5.
    """
    row: FloatArray = (-y_m - CELL_SIZE_M / 2.0) / CELL_SIZE_M
    return row


def _pixel_origin(dem: DatasetReader, cell_row: int, cell_col: int) -> tuple[int, int]:
    """Row and column of the north-west pixel of a cell on the DEM's 1 m grid.

    The cell's north edge is northing -10 i and its west edge easting 10 j; the
    grid sits on whole metres (`FineLayers`), so both are whole pixels.
    """
    transform = dem.transform
    return (
        round(transform.f) + CELL_PX * cell_row,
        CELL_PX * cell_col - round(transform.c),
    )


def _refuse_missing(ground_m: FloatArray, clutter_m: FloatArray) -> None:
    for end in (0, ground_m.size - 1):
        if np.isnan(ground_m[end]):
            raise RefusedDrawError("dem_gap", f"no 1 m ground height at profile index {end}")
    missing = np.flatnonzero(np.isnan(ground_m) | np.isnan(clutter_m))
    if missing.size:
        detail = f"profile index {missing[0]} reads a 10 m cell with a pixel without a value"
        raise RefusedDrawError("empty_cell", detail)


def _logged_cell_read(
    layers: FineLayers, cell_rows: FloatArray, cell_cols: FloatArray, max_window_cells: int
) -> _CellSamples:
    """Sample every interior point, with a log line at the start and end."""
    raster = Path(layers.dem.name).name
    log.debug("cell_read_started", raster=raster, samples=int(cell_rows.size))
    started = time.perf_counter()
    ground_m = np.empty(cell_rows.size, dtype=np.float64)
    clutter_m = np.empty(cell_rows.size, dtype=np.float64)
    filled_by_cell: dict[tuple[int, int], int] = {}
    parts = window_slices(cell_rows, cell_cols, max_window_cells, _MARGIN_CELLS)
    for part in parts:
        window = _sample_cell_window(layers, cell_rows[part], cell_cols[part])
        ground_m[part], clutter_m[part] = window.ground_m, window.clutter_m
        filled_by_cell.update(window.filled_by_cell)
    log.debug(
        "cell_read_finished",
        raster=raster,
        windows=len(parts),
        empty=int((np.isnan(ground_m) | np.isnan(clutter_m)).sum()),
        duration_s=round(time.perf_counter() - started, 6),
    )
    return _CellSamples(ground_m=ground_m, clutter_m=clutter_m, filled_by_cell=filled_by_cell)


def _sample_cell_window(
    layers: FineLayers, cell_rows: FloatArray, cell_cols: FloatArray
) -> _CellSamples:
    """Read one window of whole cells, average it and sample it bilinearly."""
    row_start = math.floor(cell_rows.min()) - _MARGIN_CELLS
    col_start = math.floor(cell_cols.min()) - _MARGIN_CELLS
    shape = (
        math.floor(cell_rows.max()) + 2 + _MARGIN_CELLS - row_start,
        math.floor(cell_cols.max()) + 2 + _MARGIN_CELLS - col_start,
    )
    pixels = (
        *_pixel_origin(layers.dem, row_start, col_start),
        shape[0] * CELL_PX,
        shape[1] * CELL_PX,
    )
    dem_m = _read_padded(layers.dem, *pixels)
    dsm_m = None if layers.dsm is None else _read_padded(layers.dsm, *pixels)
    canopy_m = None if layers.canopy is None else _read_padded(layers.canopy, *pixels)
    clutter_m = clutter_height_m(dem_m, dsm_m=dsm_m, canopy_m=canopy_m)
    local = [cell_rows - row_start, cell_cols - col_start]
    # The margin keeps every sample's four neighbouring cells inside the window,
    # so mode="nearest" never acts.
    ground = map_coordinates(_cell_means(dem_m), local, order=1, mode="nearest")
    clutter = map_coordinates(_cell_means(clutter_m), local, order=1, mode="nearest")
    filled = {} if dsm_m is None else _filled_in_read_cells(dsm_filled_mask(dem_m, dsm_m), local)
    return _CellSamples(
        ground_m=np.asarray(ground, dtype=np.float64),
        clutter_m=np.asarray(clutter, dtype=np.float64),
        filled_by_cell={(row_start + r, col_start + c): n for (r, c), n in filled.items()},
    )


def _read_padded(
    source: DatasetReader, row_start: int, col_start: int, height: int, width: int
) -> FloatArray:
    """Read a pixel window as float64, NaN for nodata and for pixels beyond the raster.

    A cell that reaches past the raster edge therefore has no value, like a cell
    holding a nodata pixel.
    """
    out = np.full((height, width), np.nan, dtype=np.float64)
    top, bottom = max(row_start, 0), min(row_start + height, source.height)
    left, right = max(col_start, 0), min(col_start + width, source.width)
    if bottom > top and right > left:
        block = source.read(1, window=Window(left, top, right - left, bottom - top), masked=True)
        inner = (
            slice(top - row_start, bottom - row_start),
            slice(left - col_start, right - col_start),
        )
        out[inner] = np.ma.filled(block.astype(np.float64), np.nan)
    return out


def _by_cell(pixels: npt.NDArray[Any]) -> npt.NDArray[Any]:
    """View pixels as (cell row, pixel row, cell column, pixel column).

    Every window is a whole number of cells, so the reshape never drops a pixel.
    """
    rows, cols = pixels.shape[0] // CELL_PX, pixels.shape[1] // CELL_PX
    return pixels.reshape(rows, CELL_PX, cols, CELL_PX)


def _cell_means(pixels: FloatArray) -> FloatArray:
    """Mean of each cell; NaN if any of its pixels is NaN."""
    means: FloatArray = _by_cell(pixels).mean(axis=(1, 3))
    return means


def _filled_in_read_cells(filled: BoolArray, local: list[FloatArray]) -> dict[tuple[int, int], int]:
    """Filled pixels per cell, for the cells a bilinear sample reads.

    Bilinear interpolation reads the four cells around each sample: the cell
    below and the one after it on each axis.
    """
    per_cell = _by_cell(filled).sum(axis=(1, 3))
    low_rows = np.floor(local[0]).astype(np.int64)
    low_cols = np.floor(local[1]).astype(np.int64)
    read = {
        (int(r) + dr, int(c) + dc)
        for r, c in zip(low_rows, low_cols, strict=True)
        for dr in (0, 1)
        for dc in (0, 1)
    }
    return {cell: int(per_cell[cell]) for cell in read}
