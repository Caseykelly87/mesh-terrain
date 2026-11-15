"""Knife-edge diffraction (ITU-R P.526) with the Bullington construction."""

import math
from pathlib import Path

import numpy as np
import pytest
from terrain_dem import centre_xy_m, write_dem

from terrain.clearance import los_clear, obstruction_m
from terrain.curvature import earth_bulge_m
from terrain.diffraction import diffraction_parameter, knife_edge_db, knife_edge_loss_db
from terrain.profile import PathProfile, sample_profile

C_M_PER_S = 299_792_458.0
FREQUENCY_MHZ = 915.0
WAVELENGTH_M = C_M_PER_S / (FREQUENCY_MHZ * 1e6)


def j_of_nu(nu: float) -> float:
    """P.526 equation (31), written out again so the test does not share the code."""
    if nu <= -0.78:
        return 0.0
    return 6.9 + 20.0 * math.log10(math.sqrt((nu - 0.1) ** 2 + 1.0) + nu - 0.1)


def nu_of(h_m: float, d1_m: float, d2_m: float) -> float:
    """P.526 equation (26)."""
    return h_m * math.sqrt(2.0 / WAVELENGTH_M * (1.0 / d1_m + 1.0 / d2_m))


def profile_from(ground_m: np.ndarray, step_m: float) -> PathProfile:
    distance = np.arange(ground_m.size) * step_m
    return PathProfile(
        distance_m=distance,
        ground_m=ground_m.astype(np.float64),
        tx_xy_m=(700_000.0, 4_280_000.0),
        rx_xy_m=(700_000.0 + float(distance[-1]), 4_280_000.0),
    )


def test_grazing_incidence_is_6_db() -> None:
    assert knife_edge_loss_db(0.0) == pytest.approx(6.0, abs=0.1)


def test_loss_is_zero_below_minus_0_78() -> None:
    assert knife_edge_loss_db(-0.78) == pytest.approx(0.0, abs=0.01)
    assert knife_edge_loss_db(-0.79) == 0.0
    assert knife_edge_loss_db(-5.0) == 0.0


def test_loss_matches_equation_31_and_grows_with_nu() -> None:
    values = [-0.5, 0.0, 0.5, 1.0, 2.4, 5.0]

    losses = [knife_edge_loss_db(nu) for nu in values]

    assert losses == pytest.approx([j_of_nu(nu) for nu in values], abs=1e-9)
    assert losses == sorted(losses)


def test_diffraction_parameter_matches_equation_26() -> None:
    assert diffraction_parameter(10.0, 3_000.0, 7_000.0, FREQUENCY_MHZ) == pytest.approx(
        nu_of(10.0, 3_000.0, 7_000.0)
    )


def test_flat_short_path_has_no_diffraction_loss() -> None:
    profile = profile_from(np.full(101, 150.0), 10.0)

    assert knife_edge_db(profile, FREQUENCY_MHZ, 30.0, 30.0) == 0.0


def test_single_ridge_matches_the_p526_formula(tmp_path: Path) -> None:
    ground, ridge_m, ridge_col = 150.0, 25.0, 710
    heights = np.full((20, 2100), ground, dtype=np.float32)
    heights[:, ridge_col] = ground + ridge_m
    dem = write_dem(tmp_path / "ridge.tif", heights)
    tx, rx = centre_xy_m(10, 10), centre_xy_m(10, 2010)
    profile = sample_profile(dem, tx, rx, step_m=1.0)
    d1, d2 = 700.0, 1_300.0
    ray_m = ground + 2.0
    bulge = d1 * d2 / (2.0 * (4.0 / 3.0) * 6_371_000.0)
    height_above_ray = ground + ridge_m + bulge - ray_m

    assert profile.ground_m[700] == ground + ridge_m
    assert not los_clear(profile, 2.0, 2.0)
    assert obstruction_m(profile, 2.0, 2.0) == pytest.approx(height_above_ray)
    assert knife_edge_db(profile, FREQUENCY_MHZ, 2.0, 2.0) == pytest.approx(
        j_of_nu(nu_of(height_above_ray, d1, d2)), abs=0.1
    )


def test_ridge_just_below_the_ray_still_costs_some_loss() -> None:
    ground = np.zeros(1001)
    ground[400] = 9.0
    profile = profile_from(ground, 1.0)

    loss = knife_edge_db(profile, FREQUENCY_MHZ, 10.0, 10.0, k_factor=math.inf)

    assert los_clear(profile, 10.0, 10.0, k_factor=math.inf)
    expected = j_of_nu(nu_of(-1.0, 400.0, 600.0))
    assert 0.0 < expected < 6.0
    assert loss == pytest.approx(expected, abs=1e-9)


def test_two_ridges_use_the_bullington_point() -> None:
    ground = np.zeros(1001)
    ground[300] = 20.0
    ground[600] = 25.0
    profile = profile_from(ground, 1.0)
    antenna_m = 10.0
    slope_from_tx = (20.0 - antenna_m) / 300.0
    slope_from_rx = (25.0 - antenna_m) / 400.0
    bullington_d = slope_from_rx * 1_000.0 / (slope_from_tx + slope_from_rx)
    bullington_h = slope_from_tx * bullington_d

    loss = knife_edge_db(profile, FREQUENCY_MHZ, antenna_m, antenna_m, k_factor=math.inf)

    assert bullington_d == pytest.approx(529.41, abs=0.01)
    expected = j_of_nu(nu_of(bullington_h, bullington_d, 1_000.0 - bullington_d))
    assert loss == pytest.approx(expected, abs=1e-9)
    assert loss > knife_edge_db(
        profile_from(np.where(np.arange(1001) == 600, 25.0, 0.0), 1.0),
        FREQUENCY_MHZ,
        antenna_m,
        antenna_m,
        k_factor=math.inf,
    )


def test_knife_edge_sees_the_40_km_bulge() -> None:
    length_m = 40_000.0
    profile = profile_from(np.zeros(401), 100.0)
    bulge = float(earth_bulge_m(np.array([length_m / 2.0]), length_m)[0])

    curved = knife_edge_db(profile, FREQUENCY_MHZ, bulge, bulge)
    flat = knife_edge_db(profile, FREQUENCY_MHZ, bulge, bulge, k_factor=math.inf)

    assert 23.0 <= bulge <= 24.0
    assert curved == pytest.approx(j_of_nu(0.0), abs=0.1)
    assert flat == pytest.approx(j_of_nu(nu_of(-bulge, 20_000.0, 20_000.0)), abs=0.1)


def test_knife_edge_loss_is_never_negative() -> None:
    rng = np.random.default_rng(3)
    for _ in range(50):
        profile = profile_from(rng.uniform(0.0, 40.0, size=201), 25.0)
        assert knife_edge_db(profile, FREQUENCY_MHZ, 2.0, 2.0) >= 0.0
