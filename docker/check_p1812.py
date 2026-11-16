"""Check P.1812 on the ITU map snapshot against 149.48 dB; run by `make check-p1812`.

The path: 10 km of flat ground at 150 m with clutter R 0, from the EPSG:26915
point of 38.63 N 90.25 W to the point 10 km grid east in EPSG:26915, 2 m
antennas at both ends, vertical polarisation, 915 MHz. With DN 39.126 and N0
324.511 at the path centre, the values the ADR 0006 spike read from the ITU
maps there, P.1812 gives 149.48 dB (`tests/test_p1812.py`). The map files
must first match the sha256 pins of `pipeline.terrain_snapshots.itu_maps`, so
only the ITU maps can pass. A different figure means the maps, Py1812 or the
wrapper changed. `--fixture` checks another map folder against a figure given
with it and skips the pins. It reads the folder and writes nothing.
"""

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
from pyproj import Transformer

from pipeline.common.logging import configure_logging
from pipeline.terrain_snapshots.itu_maps import P1812_8_MAPS
from terrain.p1812 import load_maps, p1812_loss
from terrain.profile import PathProfile

ITU_LOSS_DB = 149.48
TOLERANCE_DB = 0.01
LENGTH_M = 10_000.0
STEP_M = 10.0
GROUND_M = 150.0
ANTENNA_HEIGHT_M = 2.0
FREQUENCY_MHZ = 915.0
TX_LONLAT_DEG = (-90.25, 38.63)


def flat_profile() -> PathProfile:
    """The flat path in EPSG:26915, the receiver 10 km grid east of the transmitter."""
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:26915", always_xy=True)
    tx_x_m, tx_y_m = to_utm.transform(*TX_LONLAT_DEG)
    samples = int(round(LENGTH_M / STEP_M)) + 1
    return PathProfile(
        distance_m=np.linspace(0.0, LENGTH_M, samples),
        ground_m=np.full(samples, GROUND_M),
        clutter_m=np.zeros(samples),
        tx_xy_m=(tx_x_m, tx_y_m),
        rx_xy_m=(tx_x_m + LENGTH_M, tx_y_m),
    )


def pin_mismatches(folder: Path, pins: Mapping[str, str]) -> list[str]:
    """Map files whose manifest sha256 is missing or differs from its pin.

    Args:
        folder: The map snapshot folder holding `manifest.json`.
        pins: sha256 by file name, from `P1812_8_MAPS.members`.

    Returns:
        One line per mismatch, empty when every pin matches.
    """
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    recorded = {entry["path"]: entry["sha256"] for entry in manifest["files"]}
    return [
        f"{name}: manifest {recorded.get(name)}, pinned {pin}"
        for name, pin in pins.items()
        if recorded.get(name) != pin
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="check_p1812.py")
    parser.add_argument("maps", type=Path, help="finished P.1812 map snapshot folder")
    parser.add_argument("--fixture", action="store_true", help="not the ITU maps; skip the pins")
    parser.add_argument("--expect-db", type=float, help="the fixture's figure; needs --fixture")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Load the maps, compute the flat-path loss and compare it.

    Args:
        argv: The maps folder, then `--fixture --expect-db <dB>` for a folder
            other than the ITU snapshot; the process's arguments by default.

    Returns:
        0 when the loss is within 0.01 dB of the figure, 1 otherwise or when
        the maps fail to load or to match their pins, 2 for bad arguments.
    """
    parser = _parser()
    args = parser.parse_args(argv)
    if args.fixture != (args.expect_db is not None):
        parser.error("--fixture and --expect-db go together; the ITU maps must give 149.48 dB")
    configure_logging("console")
    try:
        maps = load_maps(args.maps)
    except RuntimeError as err:
        print(f"check-p1812: {err}", file=sys.stderr)
        return 1
    if not args.fixture and (mismatches := pin_mismatches(args.maps, P1812_8_MAPS.members)):
        print("check-p1812: not the pinned ITU maps: " + "; ".join(mismatches), file=sys.stderr)
        return 1
    expected_db = args.expect_db if args.fixture else ITU_LOSS_DB
    result = p1812_loss(
        flat_profile(),
        tx_height_m=ANTENNA_HEIGHT_M,
        rx_height_m=ANTENNA_HEIGHT_M,
        frequency_mhz=FREQUENCY_MHZ,
        maps=maps,
        polarisation="vertical",
    )
    print(f"p1812 basic transmission loss: {result.loss_db:.2f} dB from {maps.snapshot_id}")
    if abs(result.loss_db - expected_db) > TOLERANCE_DB:
        print(f"check-p1812: expected {expected_db:.2f} dB within {TOLERANCE_DB} dB")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
