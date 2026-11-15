"""ADR 0006 spike: where P.1812's troposcatter loss caps the result (eq 63).

Usage: python -I spike_floor.py

St. Louis DN 39.126 and N0 324.511, 915 MHz, p 50, vertical, zone 4. Ground is
flat at 150 m unless a case gives relief. Each line prints Lb and three terms
from Py1812's debug log: Lbs (troposcatter, eq 44), Ld50 (median diffraction,
eq 39) and Lbam (diffraction with ducting, eq 62). Eq 63 is a soft minimum of
Lbs and Lbam, so Lb close to Lbs means the cap is reached.
"""

import importlib
import importlib.resources
import io
from unittest import mock

import numpy as np

LATITUDE_DEG = 38.63
TX_LONGITUDE_DEG = -90.25
DEBUG_TERMS = ("Lbs (dB)", "Ld50 (dB)", "Lbam (dB)")


class _PlaceholderAnchor:
    """Stands in for files("Py1812"); joinpath returns an in-memory NaN npz."""

    def joinpath(self, name: str) -> io.BytesIO:
        buffer = io.BytesIO()
        nan_grid = np.full((1, 1), np.nan)
        np.savez(buffer, DN50=nan_grid, N050=nan_grid)
        buffer.seek(0)
        return buffer


class _KeptOpenLog(io.StringIO):
    """A debug log that survives Py1812 closing it."""

    def close(self) -> None:
        pass


def _import_p1812():
    real_files = importlib.resources.files

    def files(anchor):
        if anchor == "Py1812":
            return _PlaceholderAnchor()
        return real_files(anchor)

    with mock.patch("importlib.resources.files", files):
        return importlib.import_module("Py1812.P1812")


P1812 = _import_p1812()


def flat_ground(distance_m):
    return np.full_like(distance_m, 150.0)


def hills_20_m(distance_m):
    return 150.0 + 20.0 * np.sin(2.0 * np.pi * distance_m / 2500.0)


def uniform_clutter(height_m):
    return lambda distance_m: np.full_like(distance_m, height_m)


def clutter_near_tx(height_m, width_m):
    return lambda distance_m: np.where(distance_m <= width_m, height_m, 0.0)


def clutter_band(height_m, start_m, width_m):
    def clutter(distance_m):
        inside = (distance_m >= start_m) & (distance_m < start_m + width_m)
        return np.where(inside, height_m, 0.0)

    return clutter


def basic_loss(path_km, step_m, clutter, tx_height_m, rx_height_m, ground=flat_ground):
    points = int(round(path_km * 1000.0 / step_m)) + 1
    distance_km = np.linspace(0.0, path_km, points)
    distance_m = distance_km * 1000.0
    rx_longitude_deg = TX_LONGITUDE_DEG + path_km / (111.32 * np.cos(np.radians(LATITUDE_DEG)))
    log = _KeptOpenLog()
    loss_db, _ = P1812.bt_loss(
        0.915, 50, distance_km, ground(distance_m), clutter(distance_m), np.full(points, 4),
        tx_height_m, rx_height_m, 2,
        LATITUDE_DEG, LATITUDE_DEG, TX_LONGITUDE_DEG, rx_longitude_deg,
        DN=39.126, N0=324.511, debug=1, fid_log=log,
    )
    terms = {}
    for line in log.getvalue().splitlines():
        fields = line.split(",")
        if fields[0].strip() in DEBUG_TERMS:
            terms[fields[0].strip()] = float(fields[3])
    return loss_db, terms


CASES = [
    ("10 km, R 0, 2 m / 2 m", 10, 10, uniform_clutter(0.0), 2.0, 2.0, flat_ground),
    ("10 km, R 1, 2 m / 2 m", 10, 10, uniform_clutter(1.0), 2.0, 2.0, flat_ground),
    ("10 km, R 2, 2 m / 2 m", 10, 10, uniform_clutter(2.0), 2.0, 2.0, flat_ground),
    ("10 km, R 3, 2 m / 2 m", 10, 10, uniform_clutter(3.0), 2.0, 2.0, flat_ground),
    ("10 km, R 10, 2 m / 2 m", 10, 10, uniform_clutter(10.0), 2.0, 2.0, flat_ground),
    ("10 km, 8 m over first 15 m only, 10 m step", 10, 10, clutter_near_tx(8.0, 15.0), 2.0, 2.0, flat_ground),
    ("10 km, 8 m over first 15 m only, 1 m step", 10, 1, clutter_near_tx(8.0, 15.0), 2.0, 2.0, flat_ground),
    ("10 km, 4 m over first 15 m only, 1 m step", 10, 1, clutter_near_tx(4.0, 15.0), 2.0, 2.0, flat_ground),
    ("10 km, 4 m over 15 m at mid-path, 1 m step", 10, 1, clutter_band(4.0, 5000.0, 15.0), 2.0, 2.0, flat_ground),
    ("3 km, R 0, 2 m / 2 m", 3, 10, uniform_clutter(0.0), 2.0, 2.0, flat_ground),
    ("3 km, R 10, 2 m / 2 m", 3, 10, uniform_clutter(10.0), 2.0, 2.0, flat_ground),
    ("10 km, R 10, 2 m / 10 m gateway", 10, 10, uniform_clutter(10.0), 2.0, 10.0, flat_ground),
    ("10 km, R 0, 2 m / 10 m gateway", 10, 10, uniform_clutter(0.0), 2.0, 10.0, flat_ground),
    ("10 km, hills of plus or minus 20 m, R 0, 2 m / 2 m", 10, 10, uniform_clutter(0.0), 2.0, 2.0, hills_20_m),
    ("10 km, hills of plus or minus 20 m, R 10, 2 m / 2 m", 10, 10, uniform_clutter(10.0), 2.0, 2.0, hills_20_m),
]

for label, path_km, step_m, clutter, tx_height_m, rx_height_m, ground in CASES:
    loss_db, terms = basic_loss(path_km, step_m, clutter, tx_height_m, rx_height_m, ground)
    detail = ", ".join(f"{name.split()[0]} {terms[name]:.2f}" for name in DEBUG_TERMS)
    print(f"{label}: Lb {loss_db:.2f} dB ({detail})")
