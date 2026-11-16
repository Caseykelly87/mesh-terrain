"""Check P.1812 on a map snapshot against the ADR 0006 spike figure; run by `make check-p1812`.

The path is the spike's: 10 km of flat ground at 150 m with clutter R 0, from
the EPSG:26915 point of 38.63 N 90.25 W due east, 2 m antennas at both ends,
vertical polarisation, 915 MHz. With the ITU maps the path centre reads DN
39.126 and N0 324.511 and P.1812 gives 149.48 dB. A different figure means the
maps, Py1812 or the wrapper changed. It reads the snapshot and writes nothing.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from pyproj import Transformer

from pipeline.common.logging import configure_logging
from terrain.p1812 import load_maps, p1812_loss
from terrain.profile import PathProfile

SPIKE_LOSS_DB = 149.48
TOLERANCE_DB = 0.01
LENGTH_M = 10_000.0
STEP_M = 10.0
GROUND_M = 150.0
ANTENNA_HEIGHT_M = 2.0
FREQUENCY_MHZ = 915.0
TX_LONLAT_DEG = (-90.25, 38.63)


def flat_profile() -> PathProfile:
    """The spike's flat path in EPSG:26915, receiver due east of the transmitter."""
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


def main(argv: Sequence[str] | None = None) -> int:
    """Load the maps, compute the flat-path loss and compare it; returns the exit status."""
    parser = argparse.ArgumentParser(prog="check_p1812.py")
    parser.add_argument("maps", type=Path, help="finished P.1812 map snapshot folder")
    parser.add_argument("--expect-db", type=float, default=SPIKE_LOSS_DB)
    args = parser.parse_args(argv)
    configure_logging("console")
    try:
        maps = load_maps(args.maps)
    except RuntimeError as err:
        print(f"check-p1812: {err}", file=sys.stderr)
        return 1
    result = p1812_loss(
        flat_profile(),
        tx_height_m=ANTENNA_HEIGHT_M,
        rx_height_m=ANTENNA_HEIGHT_M,
        frequency_mhz=FREQUENCY_MHZ,
        maps=maps,
        polarisation="vertical",
    )
    print(f"p1812 basic transmission loss: {result.loss_db:.2f} dB from {maps.snapshot_id}")
    if abs(result.loss_db - args.expect_db) > TOLERANCE_DB:
        print(f"check-p1812: expected {args.expect_db:.2f} dB within {TOLERANCE_DB} dB")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
