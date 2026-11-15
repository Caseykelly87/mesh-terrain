"""ADR 0006 spike, run 6: is the Lbs in Py1812's debug log the troposcatter loss?

Usage: python -I spike_cap_meaning.py   (run from an empty folder)

Casey's item 2 addition asks for a test that fails if the Lbs value read from
Py1812's debug log disappears or changes meaning. This script makes the
comparisons that test will make, at the pinned commit, on synthetic paths:

1. exactly one "Lbs (dB)" line per call, holding a finite number;
2. Lbs against a direct tl_tropo call, with theta from smooth_earth_heights and
   ae from earth_rad_eff, as bt_loss builds them;
3. Lbs against equations 44 and 45 of P.1812-8, written out here;
4. the logged Lbc against equation 63 applied to the logged Lbs and Lbam;
5. Lb against equation 69, the larger of the logged Lb0p and Lbc (the location
   term is 0 at pL = 50 and sigmaL = 0).

Each check passes within TOLERANCE_DB. The log writes values with %.10g, so
logged values carry rounding of about 1e-8 dB at 100 to 200 dB.

St. Louis DN 39.126 and N0 324.511, 915 MHz, p 50, vertical, zone 4, 10 m step,
ground at 150 m unless a case gives hills.
"""

import importlib
import importlib.resources
import io
import math
import os
from unittest import mock

import numpy as np

LATITUDE_DEG = 38.63
TX_LONGITUDE_DEG = -90.25
DN = 39.126
N0 = 324.511
FREQUENCY_GHZ = 0.915
TIME_PERCENT = 50.0
STEP_M = 10.0
TOLERANCE_DB = 1e-6
LOGGED_TERMS = ("Lbs (dB)", "Lbam (dB)", "Lb0p", "Lbc (dB)")


class _PlaceholderAnchor:
    """Stands in for files("Py1812"); joinpath returns an in-memory NaN npz."""

    def joinpath(self, name):
        buffer = io.BytesIO()
        nan_grid = np.full((1, 1), np.nan)
        np.savez(buffer, DN50=nan_grid, N050=nan_grid)
        buffer.seek(0)
        return buffer


def _import_p1812():
    real_files = importlib.resources.files

    def files(anchor):
        if anchor == "Py1812":
            return _PlaceholderAnchor()
        return real_files(anchor)

    with mock.patch("importlib.resources.files", files):
        return importlib.import_module("Py1812.P1812")


P1812 = _import_p1812()


def equation_44_db(distance_km, theta_mrad):
    frequency_loss_db = 25 * math.log10(FREQUENCY_GHZ) - 2.5 * math.log10(FREQUENCY_GHZ / 2.0) ** 2
    return (190.1 + frequency_loss_db + 20 * math.log10(distance_km) + 0.573 * theta_mrad
            - 0.15 * N0 - 10.125 * math.log10(50.0 / TIME_PERCENT) ** 0.7)


def equation_63_db(lbs_db, lbam_db):
    return -5 * math.log10(10 ** (-0.2 * lbs_db) + 10 ** (-0.2 * lbam_db))


def logged_terms(log_text):
    terms = {}
    for line in log_text.splitlines():
        fields = line.split(",")
        name = fields[0].strip()
        if name in LOGGED_TERMS:
            terms.setdefault(name, []).append(float(fields[3]))
    return terms


def run_case(label, path_km, ground, clutter, antenna_m):
    points = int(round(path_km * 1000.0 / STEP_M)) + 1
    distance_km = np.linspace(0.0, path_km, points)
    ground_m = ground(distance_km * 1000.0)
    clutter_m = clutter(distance_km * 1000.0)
    rx_longitude_deg = TX_LONGITUDE_DEG + path_km / (111.32 * np.cos(np.radians(LATITUDE_DEG)))
    log = io.StringIO()
    loss_db, _ = P1812.bt_loss(
        FREQUENCY_GHZ, TIME_PERCENT, distance_km, ground_m, clutter_m, np.full(points, 4),
        antenna_m, antenna_m, 2, LATITUDE_DEG, LATITUDE_DEG, TX_LONGITUDE_DEG, rx_longitude_deg,
        DN=DN, N0=N0, debug=1, fid_log=log,
    )
    terms = logged_terms(log.getvalue())
    lbs_lines = terms.get("Lbs (dB)", [])
    lbs_db = lbs_lines[0]
    lbam_db, lb0p_db, lbc_db = terms["Lbam (dB)"][0], terms["Lb0p"][0], terms["Lbc (dB)"][0]
    effective_radius_km, _ = P1812.earth_rad_eff(DN)
    theta_mrad = P1812.smooth_earth_heights(
        distance_km, ground_m, clutter_m, antenna_m, antenna_m, effective_radius_km, FREQUENCY_GHZ
    )[13]
    total_km = distance_km[-1] - distance_km[0]
    residuals_db = {
        "tl_tropo": abs(lbs_db - P1812.tl_tropo(total_km, theta_mrad, FREQUENCY_GHZ, TIME_PERCENT, N0)),
        "eq 44": abs(lbs_db - equation_44_db(total_km, theta_mrad)),
        "eq 63": abs(lbc_db - equation_63_db(lbs_db, lbam_db)),
        "eq 69": abs(loss_db - max(lb0p_db, lbc_db)),
    }
    one_line = len(lbs_lines) == 1 and math.isfinite(lbs_db)
    passed = one_line and all(value <= TOLERANCE_DB for value in residuals_db.values())
    setter = "Lb0p" if lb0p_db >= lbc_db else "Lbc"
    print(f"{label}: Lb {loss_db:.2f} dB, Lbs {lbs_db:.2f} dB, Lb0p {lb0p_db:.2f} dB, Lbc {lbc_db:.2f} dB")
    print(f"  Lbs lines {len(lbs_lines)}; residuals " + ", ".join(
        f"{name} {value:.1e}" for name, value in residuals_db.items()))
    print(f"  Lb0p - Lbc {lb0p_db - lbc_db:.1e} dB, larger term {setter}; "
          f"checks within {TOLERANCE_DB:.0e} dB: {'pass' if passed else 'FAIL'}")
    return max(residuals_db.values()), passed


def flat_ground(distance_m):
    return np.full_like(distance_m, 150.0)


def hills_20_m(distance_m):
    return 150.0 + 20.0 * np.sin(2.0 * np.pi * distance_m / 2500.0)


def uniform_clutter(height_m):
    return lambda distance_m: np.full_like(distance_m, height_m)


def clutter_with_gap(height_m, path_m, gap_m):
    """Clutter from gap_m to path_m - gap_m inclusive, as spike_cap_position.py builds it."""
    return lambda distance_m: np.where((distance_m >= gap_m) & (distance_m <= path_m - gap_m), height_m, 0.0)


CASES = [
    ("10 km flat, R 10, 2 m antennas", 10.0, flat_ground, uniform_clutter(10.0), 2.0),
    ("10 km flat, R 0, 2 m antennas", 10.0, flat_ground, uniform_clutter(0.0), 2.0),
    ("10 km hills of plus or minus 20 m, R 10, 2 m antennas", 10.0, hills_20_m, uniform_clutter(10.0), 2.0),
    ("3 km flat, R 10 with 300 m clear at each end, 2 m antennas", 3.0, flat_ground,
     clutter_with_gap(10.0, 3000.0, 300.0), 2.0),
    ("1 km flat, R 0, 10 m antennas", 1.0, flat_ground, uniform_clutter(0.0), 10.0),
]

if __name__ == "__main__":
    before = set(os.listdir("."))
    results = [run_case(*case) for case in CASES]
    print(f"largest residual {max(r for r, _ in results):.2e} dB; "
          f"all checks pass: {all(ok for _, ok in results)}")
    print("files written to the working folder:", sorted(set(os.listdir(".")) - before))
