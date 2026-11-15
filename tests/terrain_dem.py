"""Synthetic DEM GeoTIFFs for the terrain tests.

Every raster is north-up with square pixels. Pixel (row, col) has its centre at
(ORIGIN_X_M + (col + 0.5) * res, ORIGIN_Y_M - (row + 0.5) * res) in EPSG:26915,
a point near St. Louis, so tests place endpoints on pixel centres exactly.
"""

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt
import rasterio
from rasterio.transform import from_origin

from terrain.profile import XY, PathProfile, sample_profile

if TYPE_CHECKING:
    from terrain.cells import FineLayers

ORIGIN_X_M = 720_000.0
ORIGIN_Y_M = 4_290_000.0


def write_dem(
    path: Path,
    heights_m: npt.NDArray[np.float32],
    *,
    resolution_m: float = 1.0,
    nodata: float | None = -999999.0,
    crs: str = "EPSG:26915",
    origin_xy_m: tuple[float, float] = (ORIGIN_X_M, ORIGIN_Y_M),
) -> Path:
    """Write a one-band float32 GeoTIFF, by default at the module's origin.

    `origin_xy_m` is the top-left corner of the top-left pixel, EPSG:26915.
    """
    rows, cols = heights_m.shape
    profile = {
        "driver": "GTiff",
        "height": rows,
        "width": cols,
        "count": 1,
        "dtype": "float32",
        "crs": crs,
        "transform": from_origin(*origin_xy_m, resolution_m, resolution_m),
        "nodata": nodata,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(heights_m.astype(np.float32), 1)
    return path


def centre_xy_m(row: int, col: int, resolution_m: float = 1.0) -> tuple[float, float]:
    """EPSG:26915 easting and northing of a pixel centre."""
    return (
        ORIGIN_X_M + (col + 0.5) * resolution_m,
        ORIGIN_Y_M - (row + 0.5) * resolution_m,
    )


def sample_at(path: Path, tx_xy_m: XY, rx_xy_m: XY, step_m: float, **kwargs: Any) -> PathProfile:
    """Open a DEM and sample one profile from it, as the engine will with an open raster."""
    with rasterio.open(path) as source:
        return sample_profile(source, tx_xy_m, rx_xy_m, step_m, **kwargs)


def cell_centre_xy_m(cell_row: int, cell_col: int) -> tuple[float, float]:
    """EPSG:26915 centre of a 10 m cell of a 1 m raster written by `write_dem`."""
    return (ORIGIN_X_M + 10.0 * cell_col + 5.0, ORIGIN_Y_M - 10.0 * cell_row - 5.0)


@contextmanager
def open_layers(
    dem: Path, *, dsm: Path | None = None, canopy: Path | None = None
) -> Iterator["FineLayers"]:
    """Open the 1 m layers once, as the engine does per batch."""
    from terrain.cells import FineLayers

    with ExitStack() as stack:
        yield FineLayers(
            dem=stack.enter_context(rasterio.open(dem)),
            dsm=None if dsm is None else stack.enter_context(rasterio.open(dsm)),
            canopy=None if canopy is None else stack.enter_context(rasterio.open(canopy)),
        )
