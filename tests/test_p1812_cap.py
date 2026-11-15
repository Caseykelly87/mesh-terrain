"""The troposcatter loss Lbs read from Py1812's debug log keeps its meaning (ADR 0006 item 2).

Casey's addition to item 2: `p1812_at_cap_share` reads Lbs from a debug log
line that upstream does not promise to keep, so this test fails if the value
disappears or changes meaning when the Py1812 pin moves. On synthetic paths it
makes the wrapper's own bt_loss call (debug=1, in-memory log), reads Lbs with
the wrapper's own reader, and compares, each to 1e-6 dB:

1. Lbs with Py1812's tl_tropo called directly on the same path, with theta
   from smooth_earth_heights and ae from earth_rad_eff(DN), as bt_loss builds
   them at 6c9061dd (P1812.py lines 296, 304 to 322 and 460);
2. Lbs with equations 44 and 45 of ITU-R P.1812-8, written out below;
3. the logged Lbc with equation 63 applied to the logged Lbs and Lbam;
4. Lb with equation 69, the larger of the logged Lb0p and Lbc; the location
   term is 0 at pL = 50 and sigmaL = 0 (P1812.py lines 468 to 477).

The log writes every value with %.10g, about 1e-7 dB of rounding at 160 dB,
so 1e-6 dB is above the rounding. The paths are those of spike run 6
(docs/adr/0006-spike/spike_cap_meaning.py): 915 MHz, p 50, vertical, zone 4,
10 m step, DN 39.126 and N0 324.511, ground at 150 m unless hilly.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pytest

from terrain.p1812 import P1812, BtLossInputs, call_bt_loss, troposcatter_loss_from_log

FloatArray = npt.NDArray[np.float64]

LATITUDE_DEG = 38.63
TX_LONGITUDE_DEG = -90.25
DN = 39.126
N0 = 324.511
FREQUENCY_GHZ = 0.915
TIME_PERCENT = 50.0
STEP_M = 10.0
TOLERANCE_DB = 1e-6
VERTICAL = 2


@dataclass(frozen=True)
class SyntheticPath:
    label: str
    length_km: float
    ground: Callable[[FloatArray], FloatArray]
    clutter: Callable[[FloatArray], FloatArray]
    antenna_m: float


def flat_ground(distance_m: FloatArray) -> FloatArray:
    return np.full_like(distance_m, 150.0)


def hills_20_m(distance_m: FloatArray) -> FloatArray:
    return 150.0 + 20.0 * np.sin(2.0 * np.pi * distance_m / 2500.0)


def uniform(height_m: float) -> Callable[[FloatArray], FloatArray]:
    return lambda distance_m: np.full_like(distance_m, height_m)


def with_clear_ends(
    height_m: float, path_m: float, gap_m: float
) -> Callable[[FloatArray], FloatArray]:
    return lambda d: np.where((d >= gap_m) & (d <= path_m - gap_m), height_m, 0.0)


PATHS = [
    SyntheticPath("10 km flat, R 10 m", 10.0, flat_ground, uniform(10.0), 2.0),
    SyntheticPath("10 km flat, bare", 10.0, flat_ground, uniform(0.0), 2.0),
    SyntheticPath("10 km hills, R 10 m", 10.0, hills_20_m, uniform(10.0), 2.0),
    SyntheticPath(
        "3 km flat, R 10 m, 300 m clear",
        3.0,
        flat_ground,
        with_clear_ends(10.0, 3000.0, 300.0),
        2.0,
    ),
    SyntheticPath("1 km flat, bare, 10 m antennas", 1.0, flat_ground, uniform(0.0), 10.0),
]


def inputs_for(path: SyntheticPath) -> BtLossInputs:
    points = int(round(path.length_km * 1000.0 / STEP_M)) + 1
    distance_km = np.linspace(0.0, path.length_km, points)
    rx_longitude_deg = TX_LONGITUDE_DEG + path.length_km / (
        111.32 * math.cos(math.radians(LATITUDE_DEG))
    )
    return BtLossInputs(
        distance_km=distance_km,
        ground_m=path.ground(distance_km * 1000.0),
        clutter_m=path.clutter(distance_km * 1000.0),
        tx_height_m=path.antenna_m,
        rx_height_m=path.antenna_m,
        frequency_ghz=FREQUENCY_GHZ,
        polarisation_code=VERTICAL,
        tx_lat_deg=LATITUDE_DEG,
        tx_lon_deg=TX_LONGITUDE_DEG,
        rx_lat_deg=LATITUDE_DEG,
        rx_lon_deg=rx_longitude_deg,
        dn=DN,
        n0=N0,
    )


def logged_term(log_text: str, name: str) -> float:
    """The one value the log holds for a term, from its `name,ref,,value,` line."""
    values = [
        float(line.split(",")[3])
        for line in log_text.splitlines()
        if line.split(",")[0].strip() == name
    ]
    assert len(values) == 1, f"{name} appears {len(values)} times in the log"
    return values[0]


def equation_44_db(distance_km: float, theta_mrad: float) -> float:
    """Troposcatter loss Lbs not exceeded for p percent time, P.1812-8 equations (44) and (45).

    Args:
        distance_km: Great-circle path length d, km.
        theta_mrad: Path angular distance theta, mrad (equation 82).
    """
    log_f = math.log10(FREQUENCY_GHZ)
    frequency_term_db = 25.0 * log_f - 2.5 * math.log10(FREQUENCY_GHZ / 2.0) ** 2  # (45)
    return (
        190.1
        + frequency_term_db
        + 20.0 * math.log10(distance_km)
        + 0.573 * theta_mrad
        - 0.15 * N0
        - 10.125 * math.log10(50.0 / TIME_PERCENT) ** 0.7
    )


def equation_63_db(lbs_db: float, lbam_db: float) -> float:
    """Soft minimum of troposcatter and the other mechanisms, P.1812-8 equation (63)."""
    return -5.0 * math.log10(10.0 ** (-0.2 * lbs_db) + 10.0 ** (-0.2 * lbam_db))


def theta_mrad_of(inputs: BtLossInputs) -> float:
    """Path angular distance theta in mrad, from smooth_earth_heights with ae from earth_rad_eff."""
    effective_radius_km, _ = P1812.earth_rad_eff(inputs.dn)
    return float(
        P1812.smooth_earth_heights(
            inputs.distance_km,
            inputs.ground_m,
            inputs.clutter_m,
            inputs.tx_height_m,
            inputs.rx_height_m,
            effective_radius_km,
            inputs.frequency_ghz,
        )[13]
    )


def direct_tl_tropo_db(inputs: BtLossInputs) -> float:
    """Py1812's tl_tropo on the path, with theta and ae built as bt_loss builds them."""
    total_km = float(inputs.distance_km[-1] - inputs.distance_km[0])
    return float(
        P1812.tl_tropo(
            total_km, theta_mrad_of(inputs), inputs.frequency_ghz, TIME_PERCENT, inputs.n0
        )
    )


@pytest.fixture(params=PATHS, ids=[path.label for path in PATHS])
def logged_call(request: pytest.FixtureRequest) -> tuple[BtLossInputs, float, str]:
    inputs = inputs_for(request.param)
    loss_db, log_text = call_bt_loss(inputs)
    return inputs, loss_db, log_text


def test_lbs_is_pythons_troposcatter_loss(logged_call: tuple[BtLossInputs, float, str]) -> None:
    inputs, _, log_text = logged_call
    lbs_db = troposcatter_loss_from_log(log_text)
    assert abs(lbs_db - direct_tl_tropo_db(inputs)) <= TOLERANCE_DB


def test_lbs_follows_equations_44_and_45(logged_call: tuple[BtLossInputs, float, str]) -> None:
    inputs, _, log_text = logged_call
    lbs_db = troposcatter_loss_from_log(log_text)
    total_km = float(inputs.distance_km[-1] - inputs.distance_km[0])
    assert abs(lbs_db - equation_44_db(total_km, theta_mrad_of(inputs))) <= TOLERANCE_DB


def test_lbs_caps_the_result_through_equations_63_and_69(
    logged_call: tuple[BtLossInputs, float, str],
) -> None:
    _, loss_db, log_text = logged_call
    lbs_db = troposcatter_loss_from_log(log_text)
    lbam_db = logged_term(log_text, "Lbam (dB)")
    lbc_db = logged_term(log_text, "Lbc (dB)")
    lb0p_db = logged_term(log_text, "Lb0p")
    assert abs(lbc_db - equation_63_db(lbs_db, lbam_db)) <= TOLERANCE_DB
    assert abs(loss_db - max(lb0p_db, lbc_db)) <= TOLERANCE_DB


def test_flat_path_with_clutter_sits_at_the_cap() -> None:
    """On the 10 km path with 10 m of clutter Lb is within 0.1 dB of Lbs (spike run 5)."""
    capped = inputs_for(PATHS[0])
    loss_db, log_text = call_bt_loss(capped)
    assert abs(loss_db - troposcatter_loss_from_log(log_text)) <= 0.1
    assert loss_db >= logged_term(log_text, "Lb0p")
