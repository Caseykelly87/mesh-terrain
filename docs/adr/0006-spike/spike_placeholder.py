"""ADR 0006 spike, option 2.2: import Py1812 against a placeholder npz.

Usage: python -I spike_placeholder.py <dir with N050.TXT and DN50.TXT> <scratch dir>

The placeholder holds 1 x 1 NaN grids, no ITU data. It only satisfies the map
load Py1812 runs at import. DN and N0 come from the ITU text files, read with
np.loadtxt, interpolated by Py1812's own interp2 at the point Py1812's own
great_circle_path gives as the path centre, and passed on every call.
"""

import importlib
import importlib.resources
import importlib.util
import sys
from pathlib import Path
from unittest import mock

import numpy as np


def import_redirected(folder: Path):
    if "Py1812.P1812" in sys.modules:
        raise RuntimeError("Py1812.P1812 already imported")
    spec = importlib.util.find_spec("Py1812")
    assert spec is not None and spec.origin is not None
    if (Path(spec.origin).parent / "P1812.npz").exists():
        raise RuntimeError("P1812.npz present in the installed package")
    real_files = importlib.resources.files

    def files(anchor):
        return folder if anchor == "Py1812" else real_files(anchor)

    with mock.patch("importlib.resources.files", files):
        return importlib.import_module("Py1812.P1812")


def main() -> None:
    txt_dir, scratch = Path(sys.argv[1]), Path(sys.argv[2])
    placeholder = scratch / "placeholder"
    placeholder.mkdir(parents=True, exist_ok=True)
    nan = np.full((1, 1), np.nan)
    np.savez(placeholder / "P1812.npz", DN50=nan, N050=nan)
    print(f"placeholder npz: {(placeholder / 'P1812.npz').stat().st_size} bytes")

    P1812 = import_redirected(placeholder)
    print(f"import with placeholder: ok, DN50 shape {P1812.DigitalMaps['DN50'].shape}")

    dn50 = np.loadtxt(txt_dir / "DN50.TXT")
    n050 = np.loadtxt(txt_dir / "N050.TXT")

    n = 1001
    d_km = np.linspace(0.0, 10.0, n)
    h, r, zone = np.full(n, 150.0), np.zeros(n), np.full(n, 4)
    lat, lon_t = 38.63, -90.25
    lon_r = lon_t + 10.0 / (111.32 * np.cos(np.radians(lat)))

    lam_c, phi_c, _, _ = P1812.great_circle_path(lon_r, lon_t, lat, lat, 6371, 5.0)
    dn = float(P1812.interp2(dn50, lam_c, phi_c, 1.5, 1.5))
    n0 = float(P1812.interp2(n050, lam_c, phi_c, 1.5, 1.5))
    print(f"path centre {phi_c:.5f} N {lam_c:.5f} E: DN {dn:.3f}, N0 {n0:.3f}")

    args = (0.915, 50, d_km, h, r, zone, 2.0, 2.0, 2, lat, lat, lon_t, lon_r)
    lb, ep = P1812.bt_loss(*args, DN=dn, N0=n0)
    print(f"DN and N0 passed: Lb {lb:.2f} dB  Ep {ep:.2f} dBuV/m")

    try:
        lb_bad, _ = P1812.bt_loss(*args)
        print(f"DN and N0 omitted: Lb {lb_bad} (finite: {np.isfinite(lb_bad)})")
    except Exception as err:  # the spike records whatever the fallback does
        print(f"DN and N0 omitted: {type(err).__name__}: {err}")


if __name__ == "__main__":
    main()
