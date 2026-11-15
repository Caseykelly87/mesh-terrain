"""ADR 0006 spike, option 2.2 as revised: in-memory placeholder and NaN in R.

Usage: python -I spike_inmemory.py <dir with N050.TXT and DN50.TXT>

The placeholder npz (two 1 x 1 NaN grids, no ITU data) is built in memory and
never written to disk. Then: the flat-path call with DN and N0 passed, the same
call without them, and what Py1812 does when R holds a NaN.
"""

import importlib
import importlib.resources
import importlib.util
import io
import sys
from pathlib import Path
from unittest import mock

import numpy as np


class _InMemoryPlaceholder:
    """Stands in for files("Py1812"); joinpath returns an in-memory npz."""

    def joinpath(self, name: str) -> io.BytesIO:
        if name != "P1812.npz":
            raise FileNotFoundError(name)
        buffer = io.BytesIO()
        nan = np.full((1, 1), np.nan)
        np.savez(buffer, DN50=nan, N050=nan)
        buffer.seek(0)
        return buffer


def package_dir() -> Path:
    spec = importlib.util.find_spec("Py1812")
    assert spec is not None and spec.origin is not None
    return Path(spec.origin).parent


def import_with_placeholder():
    if "Py1812.P1812" in sys.modules:
        raise RuntimeError("Py1812.P1812 already imported")
    if (package_dir() / "P1812.npz").exists():
        raise RuntimeError("P1812.npz present in the installed package")
    real_files = importlib.resources.files

    def files(anchor):
        return _InMemoryPlaceholder() if anchor == "Py1812" else real_files(anchor)

    with mock.patch("importlib.resources.files", files):
        return importlib.import_module("Py1812.P1812")


def main() -> None:
    txt_dir = Path(sys.argv[1])
    P1812 = import_with_placeholder()
    print(f"import with in-memory placeholder: ok, DN50 shape {P1812.DigitalMaps['DN50'].shape}")
    print(f"P1812.files after import is the replacement: {P1812.files.__name__ == 'files' and P1812.files is not importlib.resources.files}")
    print(f"npz in package after import: {(package_dir() / 'P1812.npz').exists()}")

    dn50 = np.loadtxt(txt_dir / "DN50.TXT")
    n050 = np.loadtxt(txt_dir / "N050.TXT")
    n = 1001
    d_km = np.linspace(0.0, 10.0, n)
    flat = np.full(n, 150.0)
    zone = np.full(n, 4)
    lat, lon_t = 38.63, -90.25
    lon_r = lon_t + 10.0 / (111.32 * np.cos(np.radians(lat)))
    lam_c, phi_c, _, _ = P1812.great_circle_path(lon_r, lon_t, lat, lat, 6371, 5.0)
    dn = float(P1812.interp2(dn50, lam_c, phi_c, 1.5, 1.5))
    n0 = float(P1812.interp2(n050, lam_c, phi_c, 1.5, 1.5))

    def lb(h, r, **kwargs):
        value, _ = P1812.bt_loss(0.915, 50, d_km, h, r, zone, 2.0, 2.0, 2,
                                 lat, lat, lon_t, lon_r, **kwargs)
        return value

    print(f"flat, R 0, DN and N0 passed: Lb {lb(flat, np.zeros(n), DN=dn, N0=n0):.2f} dB")
    try:
        lb(flat, np.zeros(n))
    except IndexError as err:
        print(f"flat, R 0, DN and N0 omitted: IndexError: {err}")

    def with_nan(r, i):
        r = r.copy()
        r[i] = np.nan
        return r

    r10 = np.full(n, 10.0)
    cases = [
        ("flat, R 10", flat, r10, 300),
        ("flat, R 0", flat, np.zeros(n), 500),
    ]
    one_building = np.zeros(n)
    one_building[500] = 30.0
    cases.append(("flat, one 30 m clutter point at 500", flat, one_building, 500))
    hills = 150.0 + 40.0 * np.sin(np.linspace(0.0, 3.0 * np.pi, n)) ** 2
    cases.append(("hilly, R 10", hills, r10, 400))
    for label, h, r, i in cases:
        base = lb(h, r, DN=dn, N0=n0)
        r_zero = r.copy()
        r_zero[i] = 0.0
        print(f"{label}: Lb {base:.6f} dB; NaN at point {i}: {lb(h, with_nan(r, i), DN=dn, N0=n0):.6f} dB; "
              f"0 at point {i}: {lb(h, r_zero, DN=dn, N0=n0):.6f} dB")


if __name__ == "__main__":
    main()
