"""The DSM thin-output guard (Casey, PR #34 and unit 5b).

A PDAL run can exit 0 having written no points, so the DSM command counts the
first returns it kept over land and fails below a quarter of what the QL2
nominal density predicts for the land area. Land is the window minus the
OpenStreetMap water polygons; a window that is all water is recorded, not
failed.
"""

import geopandas as gpd
import numpy as np
import pytest
from rasterio.transform import from_origin
from shapely.geometry import box

from pipeline.terrain_snapshots.dsm_guard import (
    NOMINAL_FIRST_RETURNS_PER_M2,
    THIN_FACTOR,
    ThinOutputError,
    WindowCount,
    check_build,
    check_window,
    land_points,
    water_mask,
)

WEST, NORTH = 720_000.0, 4_280_004.0


def _water(*polygons: tuple[float, float, float, float]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(geometry=[box(*bounds) for bounds in polygons], crs="EPSG:26915")


def test_the_stated_factor_and_density() -> None:
    assert NOMINAL_FIRST_RETURNS_PER_M2 == 2.0
    assert THIN_FACTOR == 0.25


def test_water_mask_marks_cells_whose_centre_is_in_water() -> None:
    transform = from_origin(WEST, NORTH, 1.0, 1.0)
    water = _water((WEST - 5, 4_280_000.0 - 5, WEST + 1.6, NORTH + 5))
    mask = water_mask(water, transform, (4, 4))
    assert mask[:, :2].all()
    assert not mask[:, 2:].any()


def test_water_mask_is_all_false_without_water() -> None:
    mask = water_mask(_water(), from_origin(WEST, NORTH, 1.0, 1.0), (3, 5))
    assert mask.shape == (3, 5)
    assert not mask.any()


def test_land_points_ignores_water_cells_and_empty_cells() -> None:
    count = np.array([[3.0, 4.0], [-9999.0, 5.0]], dtype=np.float32)
    water = np.array([[False, True], [False, False]])
    assert land_points(count, water, nodata=-9999.0) == 8


def test_zero_points_over_land_fails() -> None:
    with pytest.raises(ThinOutputError, match="0 first returns"):
        check_window("w00", land_area_m2=100.0, water_area_m2=0.0, points=0)


def test_fewer_than_a_quarter_of_nominal_fails_with_the_numbers() -> None:
    with pytest.raises(ThinOutputError, match=r"49 first returns.*50 needed"):
        check_window("w00", land_area_m2=100.0, water_area_m2=0.0, points=49)


def test_a_quarter_of_nominal_passes() -> None:
    counted = check_window("w00", land_area_m2=100.0, water_area_m2=0.0, points=50)
    assert counted == WindowCount("w00", 100.0, 0.0, 50, "built")


def test_water_does_not_raise_the_floor() -> None:
    # 90 m2 of water: the floor is 0.25 x 2.0 x 10 m2 of land, not of the 100 m2 window.
    counted = check_window("w00", land_area_m2=10.0, water_area_m2=90.0, points=5)
    assert counted.status == "built"
    with pytest.raises(ThinOutputError):
        check_window("w00", land_area_m2=10.0, water_area_m2=90.0, points=4)


def test_a_window_of_water_only_is_recorded_not_failed() -> None:
    counted = check_window("w00", land_area_m2=0.0, water_area_m2=100.0, points=0)
    assert counted == WindowCount("w00", 0.0, 100.0, 0, "water_only")


def test_the_whole_build_is_checked_on_its_totals() -> None:
    windows = [
        WindowCount("w00", 100.0, 0.0, 300, "built"),
        WindowCount("w01", 0.0, 100.0, 0, "water_only"),
    ]
    assert check_build(windows) == {"land_area_m2": 100.0, "water_area_m2": 100.0, "points": 300}


def test_a_build_with_no_point_at_all_fails() -> None:
    with pytest.raises(ThinOutputError, match="whole build"):
        check_build([WindowCount("w00", 0.0, 100.0, 0, "water_only")])
