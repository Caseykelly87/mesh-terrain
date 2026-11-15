"""The NTIA ITM point-to-point wrapper (ADR 0002).

The profile builder, the input checks and the status codes run everywhere.
The tests marked `needs_itm_library` call libitm.so, which exists only in the
Dagster image (ITM_LIBRARY_PATH, default /usr/local/lib/libitm.so); they skip
where it is missing. CI runs them inside the image through `make check-itm`.
"""

from pathlib import Path

import numpy as np
import pytest

from pipeline.common.settings import get_settings
from terrain.errors import RefusedDrawError
from terrain.itm import (
    ItmLibrary,
    ItmSettings,
    check_itm_status,
    itm_loss,
    itm_profile,
    load_itm_library,
)
from terrain.profile import PathProfile

FLAT_DISTANCE_M = np.linspace(0.0, 10_000.0, 1001)
FLAT_GROUND_M = np.zeros(1001)

# NTIA cmd_examples/pfl.txt at commit 183ad95b (public domain): 142 intervals of 25.6 m.
NTIA_PFL_STEP_M = 25.6
NTIA_PFL_ELEVATIONS_M = [1692.0, 1692.0] + [
    float(value)
    for value, count in (
        (1693, 6), (1694, 9), (1695, 8), (1696, 6), (1697, 10), (1698, 10), (1699, 6),
        (1700, 7), (1701, 6), (1702, 8), (1703, 9), (1704, 8), (1705, 10), (1706, 9),
        (1707, 7), (1708, 8), (1709, 5), (1710, 8),
    )
    for _ in range(count)
] + [1709.0]  # fmt: skip


FLAT_PROFILE = PathProfile(
    distance_m=FLAT_DISTANCE_M, ground_m=FLAT_GROUND_M, tx_xy_m=(0.0, 0.0), rx_xy_m=(10_000.0, 0.0)
)


@pytest.fixture(scope="module")
def itm_library() -> ItmLibrary:
    path = get_settings().itm_library_path
    if not Path(path).is_file():
        pytest.skip(f"no ITM library at {path}; it is built into the Dagster image")
    return load_itm_library(path)


# Runs everywhere


def test_profile_has_the_pfl_layout() -> None:
    pfl = itm_profile(FLAT_DISTANCE_M, FLAT_GROUND_M + 150.0)
    assert pfl.shape == (1003,)
    assert pfl[0] == 1000.0 and pfl[1] == pytest.approx(10.0)
    assert np.all(pfl[2:] == 150.0)


def test_ntia_example_profile_is_transcribed_whole() -> None:
    assert len(NTIA_PFL_ELEVATIONS_M) == 143


def test_profile_refuses_an_uneven_step() -> None:
    distance_m = FLAT_DISTANCE_M.copy()
    distance_m[500] += 3.0
    with pytest.raises(ValueError, match="uniform"):
        itm_profile(distance_m, FLAT_GROUND_M)


def test_profile_refuses_a_zero_length_path() -> None:
    with pytest.raises(ValueError, match="zero-length"):
        itm_profile(np.zeros(5), np.zeros(5))


def test_profile_refuses_fewer_than_five_samples() -> None:
    with pytest.raises(ValueError, match="five"):
        itm_profile(np.linspace(0.0, 100.0, 4), np.zeros(4))


def test_profile_refuses_a_shifted_start_and_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="start at 0"):
        itm_profile(FLAT_DISTANCE_M + 5.0, FLAT_GROUND_M)
    with pytest.raises(ValueError, match="length"):
        itm_profile(FLAT_DISTANCE_M, np.zeros(1000))


def test_missing_ground_refuses_the_draw() -> None:
    ground_m = FLAT_GROUND_M.copy()
    ground_m[1] = np.nan
    with pytest.raises(RefusedDrawError, match="ground") as refusal:
        itm_profile(FLAT_DISTANCE_M, ground_m)
    assert refusal.value.reason == "dem_gap"


def test_missing_library_names_the_install_step(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="ITM_LIBRARY_PATH"):
        load_itm_library(tmp_path / "libitm.so")


def test_default_settings_follow_adr_0002() -> None:
    settings = ItmSettings()
    assert settings.climate == 5  # continental temperate
    assert settings.surface_refractivity_n == 301.0
    assert (settings.ground_permittivity, settings.ground_conductivity_s_per_m) == (15.0, 0.005)
    assert settings.mdvar == 12
    assert (settings.confidence_pct, settings.reliability_pct) == (50.0, 50.0)


@pytest.mark.parametrize("code", [0, 1])
def test_success_codes_pass(code: int) -> None:
    check_itm_status(code)


@pytest.mark.parametrize(
    ("code", "name"), [(1000, "TX_TERMINAL_HEIGHT"), (1017, "PATH_DISTANCE"), (1999, "unknown")]
)
def test_error_codes_raise_with_their_name(code: int, name: str) -> None:
    with pytest.raises(RuntimeError, match=name):
        check_itm_status(code)


# Needs libitm.so


def test_flat_path_reproduces_the_adr_0002_figure(itm_library: ItmLibrary) -> None:
    """10 km flat at sea level, 10 m step, 915 MHz, 2 m antennas: 151.76 dB."""
    result = itm_loss(
        itm_library, FLAT_PROFILE, tx_height_m=2.0, rx_height_m=2.0, frequency_mhz=915.0
    )
    assert result.loss_db == pytest.approx(151.76, abs=0.005)
    assert result.troposcatter_loss_db is None
    assert result.itm_warning_flags is not None


def test_ntia_point_to_point_example(itm_library: ItmLibrary) -> None:
    """NTIA cmd_examples i_p2pcr.txt and o_p2pcr.txt: 114.5 dB, no warnings."""
    distance_m = np.arange(143) * NTIA_PFL_STEP_M
    profile = PathProfile(
        distance_m=distance_m,
        ground_m=np.array(NTIA_PFL_ELEVATIONS_M),
        tx_xy_m=(0.0, 0.0),
        rx_xy_m=(float(distance_m[-1]), 0.0),
    )
    result = itm_loss(
        itm_library,
        profile,
        tx_height_m=15.0,
        rx_height_m=3.0,
        frequency_mhz=3500.0,
        settings=ItmSettings(mdvar=1),
    )
    assert result.loss_db == pytest.approx(114.5, abs=0.05)
    assert result.itm_warning_flags == 0


def test_out_of_range_input_raises(itm_library: ItmLibrary) -> None:
    with pytest.raises(RuntimeError, match="FREQUENCY"):
        itm_loss(itm_library, FLAT_PROFILE, tx_height_m=2.0, rx_height_m=2.0, frequency_mhz=1.0)
