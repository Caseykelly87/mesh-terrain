"""The DSM thin-output guard: an empty or thin lidar surface never passes as a success.

PDAL can exit 0 having written no points (PR #34). Casey asked for a check that
fails a run with 0 points, or with fewer points than the lidar project's
nominal density predicts by a stated factor, and for the prediction to count
land only, so a corridor window over the Mississippi or the Missouri does not
fail for being mostly water (PR #34, unit 5b).

Numbers. The St. Louis 2017 collection is quality level QL2 under the USGS
Lidar Base Specification 1.2 (USGS lidar index, WESM), whose aggregate nominal
pulse density is at least 2 pulses per square metre. Each pulse gives one
first return, and the DSM keeps first returns only, so the prediction is 2.0
first returns per square metre of land. The stated factor is a quarter: a
window fails below 0.5 first returns per square metre of land. Three 200 m
probes inside x72y429 measured 2.7 to 5.1 kept first returns per square
metre, 5 to 10 times the floor.

Land and water are counted per 1 m cell of the window: a cell is water when
its centre lies in an OpenStreetMap water polygon (rasterio's rasterize with
all_touched off). Areas are cell counts times the cell area, in square metres
of EPSG:26915. The point count is the writers.gdal count band summed over the
land cells; in binmode each kept point lands in exactly one cell, so the sum
is the number of first returns kept over land.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import geopandas as gpd
import numpy as np
import numpy.typing as npt
from rasterio import features

from pipeline.common.snapshot import SnapshotError

NOMINAL_FIRST_RETURNS_PER_M2 = 2.0
THIN_FACTOR = 0.25
BASIS = (
    "USGS Lidar Base Specification 1.2, QL2: aggregate nominal pulse density at least 2 per m2; "
    "one first return per pulse"
)


class ThinOutputError(SnapshotError):
    """A DSM window or build holds no points, or too few, over land."""


@dataclass(frozen=True)
class WindowCount:
    """What the guard recorded for one window.

    Attributes:
        name: The window name, `r<row>c<col>`.
        land_area_m2: Window area outside OSM water, square metres.
        water_area_m2: Window area inside OSM water, square metres.
        points: First returns kept over land.
        status: `built`, or `water_only` when the window has no land.
    """

    name: str
    land_area_m2: float
    water_area_m2: float
    points: int
    status: str


def water_mask(
    water: gpd.GeoDataFrame, transform: Any, shape: tuple[int, int]
) -> npt.NDArray[np.bool_]:
    """True for each cell whose centre lies in a water polygon.

    Args:
        water: Water polygons in the grid's CRS, EPSG:26915.
        transform: The window's affine transform (rasterio's Affine).
        shape: Rows and columns of the window.
    """
    if water.empty:
        return np.zeros(shape, dtype=bool)
    burned = features.rasterize(
        ((geometry, 1) for geometry in water.geometry),
        out_shape=shape,
        transform=transform,
        fill=0,
        all_touched=False,
        dtype="uint8",
    )
    return np.asarray(burned, dtype=bool)


def land_points(
    count: npt.NDArray[np.floating], water: npt.NDArray[np.bool_], nodata: float
) -> int:
    """The writers.gdal count band summed over land cells, empty cells counting 0."""
    usable = ~water & (count != nodata) & np.isfinite(count)
    return int(count[usable].sum())


def _floor(land_area_m2: float) -> float:
    return THIN_FACTOR * NOMINAL_FIRST_RETURNS_PER_M2 * land_area_m2


def check_window(name: str, land_area_m2: float, water_area_m2: float, points: int) -> WindowCount:
    """Apply the guard to one window.

    Returns:
        The window's record; status `water_only` when it has no land.

    Raises:
        ThinOutputError: when a window with land has 0 points over it, or fewer
            than THIN_FACTOR of the nominal count for its land area.
    """
    if land_area_m2 <= 0.0:
        return WindowCount(name, 0.0, water_area_m2, points, "water_only")
    needed = _floor(land_area_m2)
    if points == 0 or points < needed:
        raise ThinOutputError(
            f"DSM window {name}: {points} first returns over {land_area_m2:.0f} m2 of land, "
            f"{needed:.0f} needed ({THIN_FACTOR} x {NOMINAL_FIRST_RETURNS_PER_M2} per m2)"
        )
    return WindowCount(name, land_area_m2, water_area_m2, points, "built")


def check_build(windows: Sequence[WindowCount]) -> dict[str, float | int]:
    """Apply the guard to the whole build's totals.

    Returns:
        land_area_m2, water_area_m2 and points summed over the windows.

    Raises:
        ThinOutputError: when the build has no point over land, or fewer than
            THIN_FACTOR of the nominal count for its total land area.
    """
    land = sum(window.land_area_m2 for window in windows)
    water = sum(window.water_area_m2 for window in windows)
    points = sum(window.points for window in windows)
    if points == 0 or points < _floor(land):
        raise ThinOutputError(
            f"the whole build has {points} first returns over {land:.0f} m2 of land, "
            f"{_floor(land):.0f} needed"
        )
    return {"land_area_m2": land, "water_area_m2": water, "points": points}
