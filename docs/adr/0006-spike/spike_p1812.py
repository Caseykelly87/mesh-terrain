"""ADR 0006 spike: Py1812 at 6c9061dd with the ITU maps outside site-packages.

Usage: python -I spike_p1812.py <dir with N050.TXT and DN50.TXT> <data dir>

Builds P1812.npz into the data dir, never into the installed package, then
imports Py1812.P1812 three ways and repeats the 2026-11-08 flat-path call.
"""

import hashlib
import importlib
import importlib.resources
import sys
import time
from pathlib import Path
from unittest import mock

import numpy as np

MAP_NAMES = ("DN50", "N050")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_npz(txt_dir: Path, data_dir: Path) -> Path:
    """Same content as upstream initiate_digital_maps.py, written to data_dir."""
    maps = {name: np.loadtxt(txt_dir / f"{name}.TXT") for name in MAP_NAMES}
    data_dir.mkdir(parents=True, exist_ok=True)
    out = data_dir / "P1812.npz"
    np.savez(out, **maps)
    return out


def package_dir() -> Path:
    spec = importlib.util.find_spec("Py1812")
    assert spec is not None and spec.origin is not None
    return Path(spec.origin).parent


def import_redirected(data_dir: Path):
    """Import Py1812.P1812 with its one map lookup pointed at data_dir."""
    real_files = importlib.resources.files

    def files(anchor):
        return data_dir if anchor == "Py1812" else real_files(anchor)

    with mock.patch("importlib.resources.files", files):
        return importlib.import_module("Py1812.P1812")


def flat_path(step_m: float):
    n = int(round(10_000 / step_m)) + 1
    d_km = np.linspace(0.0, 10.0, n)
    h = np.full(n, 150.0)
    r = np.zeros(n)
    zone = np.full(n, 4)
    return d_km, h, r, zone


def call(P1812, step_m: float, **kwargs):
    d_km, h, r, zone = flat_path(step_m)
    # St. Louis, path running east; 10 km is 0.11497 deg of longitude at 38.63 N.
    lat, lon_t = 38.63, -90.25
    lon_r = lon_t + 10.0 / (111.32 * np.cos(np.radians(lat)))
    return P1812.bt_loss(0.915, 50, d_km, h, r, zone, 2.0, 2.0, 2,
                         lat, lat, lon_t, lon_r, **kwargs)


def main() -> None:
    txt_dir, data_dir = Path(sys.argv[1]), Path(sys.argv[2])
    pkg = package_dir()
    print(f"package dir: {pkg}")
    print(f"npz in package before: {(pkg / 'P1812.npz').exists()}")

    # 1. Plain import with no maps anywhere: the upstream failure mode.
    try:
        importlib.import_module("Py1812.P1812")
        print("plain import: succeeded (unexpected)")
    except FileNotFoundError as err:
        print(f"plain import without maps: FileNotFoundError: {err.filename}")
    sys.modules.pop("Py1812.P1812", None)

    # 2. Build into the data dir and import with the redirect.
    npz = build_npz(txt_dir, data_dir)
    print(f"built {npz} ({npz.stat().st_size} bytes) sha256 {sha256(npz)}")
    for name in MAP_NAMES:
        print(f"  {name}.TXT sha256 {sha256(txt_dir / f'{name}.TXT')}")
    rebuilt = build_npz(txt_dir, data_dir / "rebuild")
    print(f"rebuilt npz identical: {sha256(rebuilt) == sha256(npz)}")
    P1812 = import_redirected(data_dir)
    print(f"maps loaded: {sorted(P1812.DigitalMaps)} shapes "
          f"{[P1812.DigitalMaps[k].shape for k in sorted(P1812.DigitalMaps)]}")

    lb, ep = call(P1812, 10.0)
    print(f"flat 10 km, 10 m step, maps:      Lb {lb:.2f} dB  Ep {ep:.2f} dBuV/m")

    # Path-centre DN and N0 the maps give for this path (bilinear, as upstream).
    lat, lon_c = 38.63, -90.25 + 5.0 / (111.32 * np.cos(np.radians(38.63)))
    dn = P1812.interp2(P1812.DigitalMaps["DN50"], lon_c, lat, 1.5, 1.5)
    n0 = P1812.interp2(P1812.DigitalMaps["N050"], lon_c, lat, 1.5, 1.5)
    print(f"path centre DN {float(dn):.3f} N-units/km, N0 {float(n0):.3f} N-units, "
          f"k50 {157.0 / (157.0 - float(dn)):.4f}")

    lb2, _ = call(P1812, 10.0, DN=float(dn), N0=float(n0))
    print(f"same call with DN, N0 passed:     Lb {lb2:.2f} dB")

    for step in (10.0, 1.0):
        t0 = time.perf_counter()
        reps = 5
        for _ in range(reps):
            lb3, _ = call(P1812, step)
        ms = (time.perf_counter() - t0) / reps * 1000
        print(f"timing {step:>4} m step ({int(10_000 / step) + 1} points): "
              f"Lb {lb3:.2f} dB, {ms:.1f} ms a call")

    print(f"npz in package after: {(pkg / 'P1812.npz').exists()}")
    print(f"package files: {sorted(p.name for p in pkg.iterdir())}")


if __name__ == "__main__":
    main()
