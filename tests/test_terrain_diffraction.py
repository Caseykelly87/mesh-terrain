"""Knife-edge diffraction, ITU-R P.526-16: J(nu) and the section 4.5.1 Bullington method."""

import math
from pathlib import Path

import numpy as np
import pytest
from terrain_dem import centre_xy_m, sample_at, write_dem

from terrain.clearance import los_clear, obstruction_m
from terrain.curvature import earth_bulge_m
from terrain.diffraction import (
    bullington_loss_db,
    diffraction_parameter,
    knife_edge_db,
    knife_edge_loss_db,
)
from terrain.profile import PathProfile

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


def lb_of(luc_db: float, length_m: float) -> float:
    """P.526-16 equation (57), Lb = Luc + (1 - exp(-Luc / 6)) (10 + 0.02 d), d in km."""
    return luc_db + (1.0 - math.exp(-luc_db / 6.0)) * (10.0 + 0.02 * length_m / 1000.0)


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
    profile = sample_at(dem, tx, rx, step_m=1.0)
    d1, d2 = 700.0, 1_300.0
    ray_m = ground + 2.0
    bulge = d1 * d2 / (2.0 * (4.0 / 3.0) * 6_371_000.0)
    height_above_ray = ground + ridge_m + bulge - ray_m

    assert profile.ground_m[700] == ground + ridge_m
    assert not los_clear(profile, 2.0, 2.0)
    assert obstruction_m(profile, 2.0, 2.0) == pytest.approx(height_above_ray)
    nu = nu_of(height_above_ray, d1, d2)
    assert knife_edge_loss_db(nu) == pytest.approx(j_of_nu(nu), abs=0.1)
    assert knife_edge_db(profile, FREQUENCY_MHZ, 2.0, 2.0) == pytest.approx(
        lb_of(j_of_nu(nu), 2_000.0), abs=0.1
    )
    # By hand: nu = 23.0536 x 0.115826 = 2.6702, J = 21.431 dB,
    # Lb = 21.431 + (1 - exp(-3.5719)) x 10.04 = 31.189 dB.
    assert knife_edge_db(profile, FREQUENCY_MHZ, 2.0, 2.0) == pytest.approx(31.189, abs=0.01)


def test_ridge_just_below_the_ray_still_costs_some_loss() -> None:
    ground = np.zeros(1001)
    ground[400] = 9.0
    profile = profile_from(ground, 1.0)

    loss = knife_edge_db(profile, FREQUENCY_MHZ, 10.0, 10.0, k_factor=math.inf)

    assert los_clear(profile, 10.0, 10.0, k_factor=math.inf)
    luc = j_of_nu(nu_of(-1.0, 400.0, 600.0))
    assert 0.0 < luc < 6.0
    assert loss == pytest.approx(lb_of(luc, 1_000.0), abs=1e-9)


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
    luc = j_of_nu(nu_of(bullington_h, bullington_d, 1_000.0 - bullington_d))
    assert loss == pytest.approx(lb_of(luc, 1_000.0), abs=1e-9)
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
    assert curved == pytest.approx(lb_of(j_of_nu(0.0), length_m), abs=0.1)
    flat_luc = j_of_nu(nu_of(-bulge, 20_000.0, 20_000.0))
    assert flat == pytest.approx(lb_of(flat_luc, length_m), abs=0.1)


def test_knife_edge_loss_is_never_negative() -> None:
    rng = np.random.default_rng(3)
    for _ in range(50):
        profile = profile_from(rng.uniform(0.0, 40.0, size=201), 25.0)
        assert knife_edge_db(profile, FREQUENCY_MHZ, 2.0, 2.0) >= 0.0


def test_bullington_correction_worked_by_hand() -> None:
    # P.526-16 equation (57) with Luc = 6 dB over 10 km:
    # 1 - exp(-1) = 0.632121; 10 + 0.02 x 10 = 10.2; 6 + 0.632121 x 10.2 = 12.4476 dB.
    assert bullington_loss_db(6.0, 10_000.0) == pytest.approx(12.4476, abs=1e-4)
    assert bullington_loss_db(0.0, 10_000.0) == 0.0


def sloped_profile(ground_points: dict[int, float]) -> PathProfile:
    """1 km at 1 m: ground 100 m, 120 m under the receiver, plus the given points."""
    ground = np.full(1001, 100.0)
    ground[-1] = 120.0
    for index, height_m in ground_points.items():
        ground[index] = height_m
    return profile_from(ground, 1.0)


def test_line_of_sight_branch_on_a_sloping_ray() -> None:
    # Tops 110 m (10 m antenna) and 150 m (30 m antenna at 120 m ground): the ray is
    # 110 + 0.04 d. Bump 126 m at 500 m: Stim = 16 / 500 = 0.032 < Str = 0.04,
    # line of sight. h = 126 - 130 = -4 m.
    # 2 / lambda = 6.104223; nu = -4 x sqrt(6.104223 x 0.004) = -0.625036
    # J = 6.9 + 20 log10(sqrt(0.725036^2 + 1) - 0.725036) = 1.053906 dB
    # Lb = 1.053906 + (1 - exp(-0.175651)) x 10.02 = 2.668021 dB
    profile = sloped_profile({500: 126.0})

    loss = knife_edge_db(profile, FREQUENCY_MHZ, 10.0, 30.0, k_factor=math.inf)

    assert nu_of(-4.0, 500.0, 500.0) == pytest.approx(-0.625036, abs=1e-6)
    assert loss == pytest.approx(2.668021, abs=1e-5)


def test_bullington_point_on_a_sloping_ray() -> None:
    # Tops 110 m (10 m antenna) and 125 m (5 m antenna at 120 m ground). Ridges of
    # 140 m at 300 m and 150 m at 600 m.
    # Stim = max(30 / 300, 40 / 600) = 0.1 > Str = 15 / 1000, trans-horizon.
    # Srim = max(15 / 700, 25 / 400) = 0.0625.
    # dbp = (125 - 110 + 0.0625 x 1000) / (0.1 + 0.0625) = 476.923 m   (54)
    # edge 110 + 0.1 x 476.923 = 157.692 m; ray 110 + 15 x 0.476923 = 117.154 m
    # h = 40.538 m; nu = 40.538 x sqrt(6.104223 x (1/476.923 + 1/523.077)) = 6.341259
    # J = 28.881261 dB; Lb = 28.881261 + (1 - exp(-4.813543)) x 10.02 = 38.819908 dB
    profile = sloped_profile({300: 140.0, 600: 150.0})

    loss = knife_edge_db(profile, FREQUENCY_MHZ, 10.0, 5.0, k_factor=math.inf)

    assert nu_of(40.538462, 476.923077, 523.076923) == pytest.approx(6.341259, abs=1e-5)
    assert loss == pytest.approx(38.819908, abs=1e-4)
