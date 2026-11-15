"""Earth bulge, line of sight, obstruction and Fresnel clearance on synthetic profiles."""

import math

import numpy as np
import pytest

from terrain.clearance import fresnel_clearance_pct, fresnel_radius_m, los_clear, obstruction_m
from terrain.curvature import EARTH_RADIUS_M, earth_bulge_m
from terrain.errors import RefusedDrawError
from terrain.profile import PathProfile

C_M_PER_S = 299_792_458.0


def flat_profile(length_m: float, step_m: float, height_m: float = 0.0) -> PathProfile:
    distance = np.linspace(0.0, length_m, round(length_m / step_m) + 1)
    return PathProfile(
        distance_m=distance,
        ground_m=np.full(distance.size, height_m),
        tx_xy_m=(700_000.0, 4_280_000.0),
        rx_xy_m=(700_000.0 + length_m, 4_280_000.0),
    )


def bulge_at_midpoint_m(length_m: float, k_factor: float = 4.0 / 3.0) -> float:
    half = length_m / 2.0
    return half * half / (2.0 * k_factor * 6_371_000.0)


def test_bulge_is_about_23_m_at_the_middle_of_40_km() -> None:
    distance = np.array([0.0, 20_000.0, 40_000.0])

    bulge = earth_bulge_m(distance, 40_000.0)

    assert EARTH_RADIUS_M == 6_371_000.0
    assert bulge[0] == 0.0
    assert bulge[2] == 0.0
    assert bulge[1] == pytest.approx(bulge_at_midpoint_m(40_000.0))
    assert 23.0 <= bulge[1] <= 24.0


def test_infinite_k_means_no_bulge() -> None:
    bulge = earth_bulge_m(np.array([0.0, 20_000.0, 40_000.0]), 40_000.0, k_factor=math.inf)

    assert np.all(bulge == 0.0)


def test_flat_short_path_with_tall_antennas_is_clear() -> None:
    profile = flat_profile(1_000.0, 10.0, height_m=150.0)

    assert los_clear(profile, 30.0, 30.0)
    assert fresnel_clearance_pct(profile, 915.0, 30.0, 30.0) == 1.0
    assert obstruction_m(profile, 30.0, 30.0) < -29.9


def test_line_of_sight_sees_the_40_km_bulge() -> None:
    profile = flat_profile(40_000.0, 100.0)
    bulge = bulge_at_midpoint_m(40_000.0)

    assert not los_clear(profile, 23.0, 23.0)
    assert los_clear(profile, 24.0, 24.0)
    assert obstruction_m(profile, 0.0, 0.0) == pytest.approx(bulge)
    assert los_clear(profile, 0.0, 0.0, k_factor=math.inf)


def test_fresnel_clearance_sees_the_40_km_bulge() -> None:
    profile = flat_profile(40_000.0, 100.0)
    bulge = bulge_at_midpoint_m(40_000.0)
    wavelength = C_M_PER_S / 915e6
    radius_mid = math.sqrt(wavelength * 20_000.0 * 20_000.0 / 40_000.0)

    grazing = fresnel_clearance_pct(profile, 915.0, bulge, bulge)
    ground_level = fresnel_clearance_pct(profile, 915.0, 0.0, 0.0)

    assert grazing == pytest.approx(0.0, abs=1e-9)
    assert ground_level == pytest.approx(-bulge / radius_mid)


def test_fresnel_radius_follows_the_closed_form() -> None:
    wavelength = C_M_PER_S / 915e6

    radius = fresnel_radius_m(np.array([2_500.0, 5_000.0]), 10_000.0, 915.0)

    assert radius[0] == pytest.approx(math.sqrt(wavelength * 2_500.0 * 7_500.0 / 10_000.0))
    assert radius[1] == pytest.approx(math.sqrt(wavelength * 2_500.0))


def test_fresnel_clearance_is_at_most_one() -> None:
    profile = flat_profile(500.0, 10.0)

    assert fresnel_clearance_pct(profile, 915.0, 200.0, 200.0) == 1.0


def test_two_sample_profile_is_a_value_error() -> None:
    profile = flat_profile(5.0, 5.0)

    with pytest.raises(ValueError, match="interior"):
        los_clear(profile, 2.0, 2.0)


def test_missing_ground_value_is_refused_not_a_value_error() -> None:
    flat = flat_profile(1_000.0, 10.0)
    ground = flat.ground_m.copy()
    ground[40] = np.nan
    profile = PathProfile(
        distance_m=flat.distance_m, ground_m=ground, tx_xy_m=flat.tx_xy_m, rx_xy_m=flat.rx_xy_m
    )

    with pytest.raises(RefusedDrawError) as raised:
        los_clear(profile, 2.0, 2.0)
    assert not isinstance(raised.value, ValueError)
    assert raised.value.reason == "dem_gap"
