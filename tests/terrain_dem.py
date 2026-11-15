"""Synthetic DEM GeoTIFFs for the terrain tests.

Every raster is north-up with square pixels. Pixel (row, col) has its centre at
(ORIGIN_X_M + (col + 0.5) * res, ORIGIN_Y_M - (row + 0.5) * res) in EPSG:26915,
a point near St. Louis, so tests place endpoints on pixel centres exactly.
"""

from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import rasterio
from rasterio.transform import from_origin

from terrain.profile import XY, PathProfile, sample_profile

ORIGIN_X_M = 720_000.0
ORIGIN_Y_M = 4_290_000.0


def write_dem(
    path: Path,
    heights_m: npt.NDArray[np.float32],
    *,
    resolution_m: float = 1.0,
    nodata: float | None = -999999.0,
    crs: str = "EPSG:26915",
) -> Path:
    """Write a one-band float32 GeoTIFF with the module's origin."""
    rows, cols = heights_m.shape
    profile = {
        "driver": "GTiff",
        "height": rows,
        "width": cols,
        "count": 1,
        "dtype": "float32",
        "crs": crs,
        "transform": from_origin(ORIGIN_X_M, ORIGIN_Y_M, resolution_m, resolution_m),
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
