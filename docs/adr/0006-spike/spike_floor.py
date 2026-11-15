"""ADR 0006 spike: where P.1812 reaches its troposcatter floor (eq 63).

Usage: python -I spike_floor.py
Flat ground at 150 m, St. Louis DN 39.126 and N0 324.511, 915 MHz, p 50.
Prints Lb and Lbs (troposcatter, eq 44); Lb equal to Lbs means the floor.
"""
import importlib, importlib.resources, io
from unittest import mock
import numpy as np

class _P:
    def joinpath(self, name):
        b = io.BytesIO(); nan = np.full((1, 1), np.nan)
        np.savez(b, DN50=nan, N050=nan); b.seek(0); return b

rf = importlib.resources.files
with mock.patch("importlib.resources.files", lambda a: _P() if a == "Py1812" else rf(a)):
    P1812 = importlib.import_module("Py1812.P1812")

def run(dist_km, step_m, r_fn, htg, hrg):
    n = int(round(dist_km * 1000 / step_m)) + 1
    d = np.linspace(0.0, dist_km, n)
    lat, lt = 38.63, -90.25
    lr = lt + dist_km / (111.32 * np.cos(np.radians(lat)))
    r = r_fn(d * 1000.0)
    lb, _ = P1812.bt_loss(0.915, 50, d, np.full(n, 150.0), r, np.full(n, 4), htg, hrg, 2,
                          lat, lat, lt, lr, DN=39.126, N0=324.511)
    return lb

const = lambda v: (lambda x: np.full_like(x, v))
near_tx = lambda v, w: (lambda x: np.where(x <= w, v, 0.0))
cases = [
    ("10 km, R 0, 2 m / 2 m", 10, 10, const(0.0), 2, 2),
    ("10 km, R 1, 2 m / 2 m", 10, 10, const(1.0), 2, 2),
    ("10 km, R 2, 2 m / 2 m", 10, 10, const(2.0), 2, 2),
    ("10 km, R 3, 2 m / 2 m", 10, 10, const(3.0), 2, 2),
    ("10 km, 8 m over first 15 m only, 10 m step", 10, 10, near_tx(8.0, 15.0), 2, 2),
    ("10 km, 8 m over first 15 m only, 1 m step", 10, 1, near_tx(8.0, 15.0), 2, 2),
    ("10 km, 4 m over first 15 m only, 1 m step", 10, 1, near_tx(4.0, 15.0), 2, 2),
    ("3 km, R 0, 2 m / 2 m", 3, 10, const(0.0), 2, 2),
    ("3 km, R 10, 2 m / 2 m", 3, 10, const(10.0), 2, 2),
    ("10 km, R 10, 2 m / 10 m gateway", 10, 10, const(10.0), 2, 10),
    ("10 km, R 0, 2 m / 10 m gateway", 10, 10, const(0.0), 2, 10),
]
for label, dk, st, fn, ht, hr in cases:
    print(f"{label}: Lb {run(dk, st, fn, ht, hr):.2f} dB")
