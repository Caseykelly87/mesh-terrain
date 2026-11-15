"""ADR 0006 spike: clutter at the terminals, and the profile spacing.

Usage: python -I spike_terminal.py

Same in-memory placeholder import as spike_inmemory.py; DN and N0 are the
St. Louis path-centre values that run uses (39.126, 324.511).
"""

import importlib
import importlib.resources
import io
from unittest import mock

import numpy as np


class _Placeholder:
    def joinpath(self, name):
        buffer = io.BytesIO()
        nan = np.full((1, 1), np.nan)
        np.savez(buffer, DN50=nan, N050=nan)
        buffer.seek(0)
        return buffer


real_files = importlib.resources.files
with mock.patch("importlib.resources.files",
                lambda a: _Placeholder() if a == "Py1812" else real_files(a)):
    P1812 = importlib.import_module("Py1812.P1812")

LAT, LON_T = 38.63, -90.25
LON_R = LON_T + 10.0 / (111.32 * np.cos(np.radians(LAT)))


def lb(step_m, r_of_distance):
    n = int(round(10_000 / step_m)) + 1
    d_km = np.linspace(0.0, 10.0, n)
    r = r_of_distance(d_km * 1000.0)
    value, _ = P1812.bt_loss(0.915, 50, d_km, np.full(n, 150.0), r, np.full(n, 4),
                             2.0, 2.0, 2, LAT, LAT, LON_T, LON_R, DN=39.126, N0=324.511)
    return value


def bare(x):
    return np.zeros_like(x)


def roof_at_tx(x):
    # An 8 m building covering the first 15 m of the path: the antenna's own
    # pixel and its neighbours.
    return np.where(x <= 15.0, 8.0, 0.0)


def suburban(x):
    return np.full_like(x, 10.0)


def blocks(x):
    # 10 m buildings 15 m long every 40 m, open ground between them.
    return np.where((x % 40.0) < 15.0, 10.0, 0.0)


for step in (10.0, 1.0):
    print(f"step {step:>4} m: bare {lb(step, bare):.2f} dB")
    only_terminal = lambda x, s=step: np.where(x < s / 2, 8.0, 0.0)  # noqa: E731
    print(f"step {step:>4} m: 8 m at the Tx point only (index 0) {lb(step, only_terminal):.2f} dB")
    print(f"step {step:>4} m: 8 m over the first 15 m {lb(step, roof_at_tx):.2f} dB")
    print(f"step {step:>4} m: 10 m everywhere {lb(step, suburban):.2f} dB")
    print(f"step {step:>4} m: 10 m blocks, 15 m of every 40 m {lb(step, blocks):.2f} dB")
