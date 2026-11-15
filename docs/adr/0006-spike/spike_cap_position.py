"""ADR 0006 spike: where clutter has to stand for P.1812 to reach its cap.

Usage: python -I spike_cap_position.py

Flat paths at 150 m, 2 m antennas, 10 m step, St. Louis DN 39.126 and N0
324.511, 915 MHz, p 50. Each line prints Lb and Lbs (troposcatter, eq 44) from
Py1812's debug log, and Lbs minus Lb; within 0.1 dB counts as at the cap.
"""

import importlib
import importlib.resources
import io
from unittest import mock

import numpy as np

LATITUDE_DEG = 38.63
TX_LONGITUDE_DEG = -90.25
STEP_M = 10.0


class _PlaceholderAnchor:
    def joinpath(self, name: str) -> io.BytesIO:
        buffer = io.BytesIO()
        nan_grid = np.full((1, 1), np.nan)
        np.savez(buffer, DN50=nan_grid, N050=nan_grid)
        buffer.seek(0)
        return buffer


class _KeptOpenLog(io.StringIO):
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


def basic_loss(path_km, clutter_height_m, start_m, end_m):
    points = int(round(path_km * 1000.0 / STEP_M)) + 1
    distance_km = np.linspace(0.0, path_km, points)
    distance_m = distance_km * 1000.0
    inside = (distance_m >= start_m) & (distance_m <= end_m)
    clutter_m = np.where(inside, clutter_height_m, 0.0)
    rx_longitude_deg = TX_LONGITUDE_DEG + path_km / (111.32 * np.cos(np.radians(LATITUDE_DEG)))
    log = _KeptOpenLog()
    loss_db, _ = P1812.bt_loss(
        0.915, 50, distance_km, np.full(points, 150.0), clutter_m, np.full(points, 4),
        2.0, 2.0, 2,
        LATITUDE_DEG, LATITUDE_DEG, TX_LONGITUDE_DEG, rx_longitude_deg,
        DN=39.126, N0=324.511, debug=1, fid_log=log,
    )
    troposcatter_db = float("nan")
    for line in log.getvalue().splitlines():
        fields = line.split(",")
        if fields[0].strip() == "Lbs (dB)":
            troposcatter_db = float(fields[3])
    return loss_db, troposcatter_db


def report(label, path_km, clutter_height_m, start_m, end_m):
    loss_db, troposcatter_db = basic_loss(path_km, clutter_height_m, start_m, end_m)
    print(f"  {label}: Lb {loss_db:.2f} dB, Lbs {troposcatter_db:.2f} dB, "
          f"Lbs - Lb {troposcatter_db - loss_db:.2f} dB")


def cleared_ends(path_km, gap_m):
    return gap_m, path_km * 1000.0 - gap_m


print("10 km path, 3 m clutter in bands (start to end, metres from the transmitter):")
for start_m, end_m in [(1000.0, 9000.0), (2500.0, 7500.0), (100.0, 9900.0), (0.0, 200.0), (0.0, 10000.0)]:
    report(f"{start_m:.0f} to {end_m:.0f} m", 10.0, 3.0, start_m, end_m)

print("10 km path, clutter everywhere except gap_m at each end:")
for height_m in (3.0, 10.0):
    for gap_m in (0.0, 50.0, 100.0, 200.0, 300.0, 500.0, 1000.0, 2000.0, 4000.0):
        report(f"{height_m:.0f} m, gap {gap_m:.0f} m", 10.0, height_m, *cleared_ends(10.0, gap_m))

print("10 km path, clutter only within reach_m of the transmitter:")
for height_m in (3.0, 10.0):
    for reach_m in (20.0, 50.0, 100.0, 200.0, 500.0):
        report(f"{height_m:.0f} m, first {reach_m:.0f} m", 10.0, height_m, 0.0, reach_m)

print("10 km path, 10 m clutter away from the ends:")
report("10 m over 2.5 to 7.5 km", 10.0, 10.0, 2500.0, 7500.0)
report("10 m, 15 m band at mid-path", 10.0, 10.0, 5000.0, 5015.0)
report("10 m, 15 m band 1 km from the transmitter", 10.0, 10.0, 1000.0, 1015.0)

print("3 km and 5 km paths, 10 m clutter except gap_m at each end:")
for gap_m in (0.0, 300.0, 500.0, 1000.0):
    report(f"3 km, gap {gap_m:.0f} m", 3.0, 10.0, *cleared_ends(3.0, gap_m))
for gap_m in (0.0, 1000.0):
    report(f"5 km, gap {gap_m:.0f} m", 5.0, 10.0, *cleared_ends(5.0, gap_m))
