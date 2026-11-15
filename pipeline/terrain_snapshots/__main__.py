"""Write one terrain snapshot: `python -m pipeline.terrain_snapshots <command> [options]`.

Commands, each writing one snapshot under the snapshots folder (SNAPSHOTS_DIR,
or --root) and refusing an id that exists finished or partial:

- dem: the 1 m DEM tile x72y429 and its TNM metadata.
- osm --date YYYY-MM-01: Geofabrik's Missouri and Illinois extracts of that
  date, with their building and water polygons.
- itu-maps: the ITU-R P.1812 map zip and its two maps (`make p1812-maps`).
- dsm --osm-snapshot <id> --scratch <dir>: the first-return DSM over x72y429
  through `make pdal`; needs the OSM snapshot first; window rasters go to
  <dir>, which must lie outside the snapshots folder.
- canopy: the CHMv2 canopy tile 0231113110 and its imagery dates.
- register <id>: record a finished snapshot in raw.snapshot (snp_01), after
  checking every file against its manifest; the only command, with the DSM's
  check of its OSM snapshot, that opens the warehouse.

Nothing here is scheduled. Writing a real snapshot is permanent and runs only
after Casey's yes on its checklist.
"""

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from pipeline.common.logging import configure_logging
from pipeline.common.settings import get_settings
from pipeline.terrain_snapshots.canopy import build_canopy
from pipeline.terrain_snapshots.dem import build_dem
from pipeline.terrain_snapshots.dsm import build_dsm
from pipeline.terrain_snapshots.itu_maps import build_itu_maps
from pipeline.terrain_snapshots.osm import build_osm
from pipeline.terrain_snapshots.registry import connect, register_snapshot


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m pipeline.terrain_snapshots")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("dem", "itu-maps", "canopy"):
        commands.add_parser(name)
    osm = commands.add_parser("osm")
    osm.add_argument("--date", required=True, type=date.fromisoformat, help="YYYY-MM-01")
    dsm = commands.add_parser("dsm")
    dsm.add_argument("--osm-snapshot", required=True, help="finished OSM snapshot id")
    dsm.add_argument("--scratch", required=True, type=Path, help="folder outside the snapshots")
    register = commands.add_parser("register")
    register.add_argument("snapshot_id", help="finished snapshot id")
    for sub in commands.choices.values():
        sub.add_argument("--root", type=Path, help="snapshots folder; SNAPSHOTS_DIR by default")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the arguments and write the snapshot; returns the exit status."""
    args = _parser().parse_args(argv)
    configure_logging("console")
    root: Path = args.root or get_settings().snapshots_dir
    if args.command == "dem":
        build_dem(root)
    elif args.command == "itu-maps":
        build_itu_maps(root)
    elif args.command == "canopy":
        build_canopy(root)
    elif args.command == "osm":
        build_osm(root, args.date)
    elif args.command == "register":
        with connect() as conn:
            register_snapshot(conn, root, args.snapshot_id)
    else:
        build_dsm(root, args.osm_snapshot, args.scratch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
