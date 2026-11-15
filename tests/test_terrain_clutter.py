"""Per-pixel clutter rule of ADR 0006 item 6 (a) with the canopy fill of item 11 (i)."""

import numpy as np
import pytest

from terrain.clutter import clutter_height_m, dsm_filled_mask, require_complete_clutter
from terrain.errors import RefusedDrawError
from terrain.profile import PathProfile

NAN = np.nan


def one(value: float) -> np.ndarray:
    return np.array([value], dtype=np.float64)


def rule(dem: float, dsm: float, canopy: float) -> float:
    return float(clutter_height_m(one(dem), dsm_m=one(dsm), canopy_m=one(canopy))[0])


def test_surface_minus_ground_where_canopy_is_zero() -> None:
    assert rule(dem=150.0, dsm=158.0, canopy=0.0) == pytest.approx(8.0)


def test_negative_surface_minus_ground_is_floored_at_zero() -> None:
    assert rule(dem=150.0, dsm=149.7, canopy=0.0) == 0.0


def test_floor_comes_before_the_canopy_comparison() -> None:
    # A floored -0.3 m is 0, so any nonzero canopy wins, and a zero canopy leaves 0.
    assert rule(dem=150.0, dsm=149.7, canopy=4.0) == pytest.approx(4.0)
    assert rule(dem=150.0, dsm=149.7, canopy=0.0) == 0.0


def test_surface_with_no_value_over_ground_with_a_value_is_zero() -> None:
    assert rule(dem=150.0, dsm=NAN, canopy=0.0) == 0.0


def test_surface_with_no_value_still_takes_a_nonzero_canopy() -> None:
    assert rule(dem=150.0, dsm=NAN, canopy=12.0) == pytest.approx(12.0)


def test_canopy_with_no_value_counts_as_zero() -> None:
    assert rule(dem=150.0, dsm=156.0, canopy=NAN) == pytest.approx(6.0)
    assert rule(dem=150.0, dsm=149.0, canopy=NAN) == 0.0


def test_larger_nonzero_canopy_replaces_the_lidar_height() -> None:
    assert rule(dem=150.0, dsm=152.0, canopy=15.0) == pytest.approx(15.0)


def test_smaller_canopy_leaves_the_lidar_height() -> None:
    assert rule(dem=150.0, dsm=158.0, canopy=3.0) == pytest.approx(8.0)


def test_ground_with_no_value_leaves_clutter_undefined() -> None:
    assert np.isnan(rule(dem=NAN, dsm=158.0, canopy=0.0))
    assert np.isnan(rule(dem=NAN, dsm=NAN, canopy=15.0))


def test_worked_example_half_roof_half_tree_averages_to_11_5_m() -> None:
    # ADR 0006 item 6: half the cell is roof (DSM minus DEM 8 m, no canopy), half
    # is a tree seen leaf-off by the lidar (2 m) and at 15 m by the canopy map.
    dem = np.full((10, 10), 150.0)
    dsm = dem.copy()
    canopy = np.zeros((10, 10))
    dsm[:, :5] += 8.0
    dsm[:, 5:] += 2.0
    canopy[:, 5:] = 15.0

    clutter = clutter_height_m(dem, dsm_m=dsm, canopy_m=canopy)

    assert clutter.mean() == pytest.approx(11.5)


def test_without_a_canopy_layer_clutter_is_the_floored_lidar_height() -> None:
    dem = np.array([150.0, 150.0, 150.0])
    dsm = np.array([158.0, 149.0, NAN])

    clutter = clutter_height_m(dem, dsm_m=dsm, canopy_m=None)

    assert clutter.tolist() == pytest.approx([8.0, 0.0, 0.0])


def test_without_a_surface_layer_clutter_is_the_canopy_or_zero() -> None:
    dem = np.array([150.0, 150.0, 150.0])
    canopy = np.array([15.0, 0.0, NAN])

    clutter = clutter_height_m(dem, dsm_m=None, canopy_m=canopy)

    assert clutter.tolist() == pytest.approx([15.0, 0.0, 0.0])


def test_rule_needs_a_surface_or_a_canopy_layer() -> None:
    with pytest.raises(ValueError, match="DSM or a canopy"):
        clutter_height_m(np.zeros(3), dsm_m=None, canopy_m=None)


def test_layers_must_share_a_shape() -> None:
    with pytest.raises(ValueError, match="shape"):
        clutter_height_m(np.zeros(3), dsm_m=np.zeros(4), canopy_m=None)
    with pytest.raises(ValueError, match="shape"):
        clutter_height_m(np.zeros(3), dsm_m=None, canopy_m=np.zeros((3, 1)))


def test_filled_mask_marks_surface_gaps_over_ground_with_a_value() -> None:
    dem = np.array([150.0, 150.0, NAN, NAN])
    dsm = np.array([151.0, NAN, 151.0, NAN])

    assert dsm_filled_mask(dem, dsm).tolist() == [False, True, False, False]


def profile_with_clutter(clutter: list[float] | None) -> PathProfile:
    distance = np.arange(4, dtype=np.float64) * 10.0
    return PathProfile(
        distance_m=distance,
        ground_m=np.full(4, 150.0),
        tx_xy_m=(0.0, 0.0),
        rx_xy_m=(30.0, 0.0),
        clutter_m=None if clutter is None else np.array(clutter, dtype=np.float64),
    )


def test_profile_with_a_missing_clutter_height_is_refused_as_clutter_nan() -> None:
    with pytest.raises(RefusedDrawError) as caught:
        require_complete_clutter(profile_with_clutter([0.0, 3.0, NAN, 0.0]))

    assert caught.value.reason == "clutter_nan"


def test_complete_clutter_and_no_clutter_both_pass() -> None:
    require_complete_clutter(profile_with_clutter([0.0, 3.0, 4.0, 0.0]))
    require_complete_clutter(profile_with_clutter(None))
