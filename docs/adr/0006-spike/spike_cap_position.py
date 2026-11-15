"""ADR 0006 spike: where clutter has to stand for P.1812 to reach its cap.

Usage: python -I spike_cap_position.py

Flat 10 km path at 150 m, 2 m antennas, 10 m step, St. Louis DN 39.126 and
N0 324.511, 915 MHz, p 50. Lbs on this path is 160.17 dB.
"""

import importlib
import importlib.resources
import io
from unittest import mock

import numpy as np

LATITUDE_DEG = 38.63
TX_LONGITUDE_DEG = -90.25
PATH_KM = 10.0
STEP_M = 10.0


class _PlaceholderAnchor:
    def joinpath(self, name: str) -> io.BytesIO:
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


def basic_loss(clutter_height_m, start_m, end_m):
    points = int(round(PATH_KM * 1000.0 / STEP_M)) + 1
    distance_km = np.linspace(0.0, PATH_KM, points)
    distance_m = distance_km * 1000.0
    inside = (distance_m >= start_m) & (distance_m <= end_m)
    clutter_m = np.where(inside, clutter_height_m, 0.0)
    rx_longitude_deg = TX_LONGITUDE_DEG + PATH_KM / (111.32 * np.cos(np.radians(LATITUDE_DEG)))
    loss_db, _ = P1812.bt_loss(
        0.915, 50, distance_km, np.full(points, 150.0), clutter_m, np.full(points, 4),
        2.0, 2.0, 2,
        LATITUDE_DEG, LATITUDE_DEG, TX_LONGITUDE_DEG, rx_longitude_deg,
        DN=39.126, N0=324.511,
    )
    return loss_db


print("Bands of clutter, start and end in metres from the transmitter:")
for height_m, start_m, end_m in [
    (3.0, 1000.0, 9000.0),
    (3.0, 2500.0, 7500.0),
    (3.0, 100.0, 9900.0),
    (3.0, 0.0, 200.0),
    (3.0, 0.0, 10000.0),
]:
    print(f"  {height_m:.0f} m over {start_m:.0f} to {end_m:.0f} m: Lb {basic_loss(height_m, start_m, end_m):.2f} dB")

print("Clutter everywhere except the first and last gap_m metres:")
for height_m in (3.0, 10.0):
    for gap_m in (0.0, 50.0, 100.0, 200.0, 300.0, 500.0, 1000.0):
        loss_db = basic_loss(height_m, gap_m, PATH_KM * 1000.0 - gap_m)
        print(f"  {height_m:.0f} m, gap {gap_m:.0f} m: Lb {loss_db:.2f} dB")

print("Clutter only within reach_m metres of the transmitter:")
for height_m in (3.0, 10.0):
    for reach_m in (20.0, 50.0, 100.0, 200.0, 500.0):
        print(f"  {height_m:.0f} m, first {reach_m:.0f} m: Lb {basic_loss(height_m, 0.0, reach_m):.2f} dB")
