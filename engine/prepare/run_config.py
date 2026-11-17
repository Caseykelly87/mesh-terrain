"""The run config of a prepared tile run, with the source of every value.

Each value names where it comes from:

- frequency 906.875 MHz: the US LongFast preset the plan fixes
  (`docs/PLAN.md`, Objective), whose frequency `docs/INTERFACE.md` gives in
  its `frequency_mhz` row;
- the five models `docs/INTERFACE.md` lists, all of which the lead's brief
  for this run names (issue #44);
- `mesh_node` at the receivers; the profile at the gateway sites is open
  for Casey (PR #48) and set in `SITE_PROFILE` alone, so his answer is a
  one-line change;
- 16 endpoint draws (`docs/PLAN.md`, Phase 3 tasks; ADR 0006 item 10);
- the five snapshots registered for the tile (issue #44, PR #46);
- `output_dir: /runs`, the folder `make engine` mounts (PR #43).

The seed is fixed so the run reproduces (`snp_01`); it is the seed of the
committed fixture run and of the INTERFACE manifest example. Calibration is
null: the observations layer is not part of this run.
"""

from engine.config import SNAPSHOT_ROLES
from terrain.path_loss import MODELS

FREQUENCY_MHZ = 906.875
SITE_PROFILE = "mesh_node"
"""Profile at the gateway sites: open for Casey on PR #48 (mesh_node or site_gateway)."""
RECEIVER_PROFILE = "mesh_node"
ENDPOINT_DRAWS = 16
SEED = 20261108
OUTPUT_DIR = "/runs"
SITES_FILE = "tx_sites.parquet"
RECEIVERS_FILE = "rx_points.parquet"

NEVER_PUBLISHED = "inherit the rule that precise MeshSTL positions are never published"
"""Casey's words, issue #44, used verbatim wherever the rule is stated."""

TILE_SNAPSHOTS = {
    "x72y429": {
        "dem": "3dep-1m-mo-saintlouis-2017-x72y429",
        "dsm": "ept-mo-stlouis-2017-x72y429",
        "canopy": "canopy-v2-0231113110",
        "buildings": "osm-geofabrik-mo-il-2026-11-01",
        "p1812_maps": "itu-r-p1812-8-202509",
    },
}
"""Snapshot ids by role for each tile with a defined run (issue #44, PR #46)."""


def config_name(tile: str) -> str:
    """The config file's name: the tile name, which is also the run id."""
    return f"{tile}.yaml"


def config_text(tile: str) -> str:
    """The YAML config of the tile's run, every field explicit (`docs/INTERFACE.md`).

    Args:
        tile: A tile name in `TILE_SNAPSHOTS`.

    Returns:
        The file's text.

    Raises:
        KeyError: for a tile without a defined run.
    """
    snapshots = TILE_SNAPSHOTS[tile]
    snapshot_lines = [f"  {role}: {snapshots[role]}" for role in SNAPSHOT_ROLES]
    lines = [
        f"# The {tile} engine run (issue #44, Casey's Q1 (b) and Q2 (a)), written by",
        f"# python -m engine.prepare {tile}. Sites are real gateways and receivers are one",
        "# real relay end plus a made-up grid; every id is a pseudonym or a grid id.",
        "# This folder holds real positions and stays outside git.",
        "# Coverage outputs built from real gateway positions "
        f"{NEVER_PUBLISHED} (Casey, issue #44).",
        "# Frequency: US LongFast (docs/PLAN.md, Objective; docs/INTERFACE.md, frequency_mhz).",
        "# Profiles: the site profile is open for Casey (PR #48).",
        f"run_id: {tile}",
        f"frequency_mhz: {FREQUENCY_MHZ}",
        f"models: [{', '.join(MODELS)}]",
        f"tx_profile: {SITE_PROFILE}",
        f"rx_profile: {RECEIVER_PROFILE}",
        f"tx_sites: {SITES_FILE}",
        f"rx_points: {RECEIVERS_FILE}",
        "snapshots:",
        *snapshot_lines,
        f"endpoint_draws: {ENDPOINT_DRAWS}",
        f"seed: {SEED}",
        "calibration: null",
        f"output_dir: {OUTPUT_DIR}",
    ]
    return "\n".join(lines) + "\n"
