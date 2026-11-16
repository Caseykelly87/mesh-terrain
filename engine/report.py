"""`run_report.md`: what a run met that no column holds.

Casey asked for these per run, never as columns (PR #30, PR #24):

- refused draws by cause, under the names of the `terrain/` README: missing
  map data, empty 10 m cell, missing clutter, too-short profile;
- canopy pixels without a value per canopy tile, inside the run extent, and
  per corridor once corridors exist; those pixels count as canopy 0 in the
  clutter rule (ADR 0006 item 11 (i));
- the median, over pairs, of each pair's median count of DSM gaps filled with
  clutter 0 in the 10 m cells P.1812's profile read.

It also counts zero-length pairs, pairs with every draw refused, and pairs
whose Okumura-Hata variant would change if the 0.20 threshold moved anywhere
between 0.15 and 0.25 (ADR 0006 option 7). The text holds no time or path, so
two runs of one config write the same file.
"""

from typing import get_args

import numpy as np

from engine.predict import PairPrediction
from engine.terrain_build import TerrainBuild
from terrain.errors import RefusalReason

CAUSE_NAMES: dict[RefusalReason, str] = {
    "dem_gap": "missing map data",
    "empty_cell": "empty 10 m cell",
    "clutter_nan": "missing clutter",
    "short_profile": "too-short profile",
}
THRESHOLD_BAND = (0.15, 0.25)


def report_markdown(run_id: str, pairs: list[PairPrediction], terrain: TerrainBuild) -> str:
    """The run report for one run.

    Args:
        run_id: The run's id.
        pairs: Every pair's prediction.
        terrain: The run's terrain build.

    Returns:
        Markdown text ending in a newline.
    """
    sections = [
        _summary(run_id, pairs, terrain),
        _refusals(pairs),
        _canopy(terrain),
        _dsm_filled(pairs),
        _hata_band(pairs),
    ]
    return "\n".join(sections)


def _summary(run_id: str, pairs: list[PairPrediction], terrain: TerrainBuild) -> str:
    summaries = [p.summary for p in pairs if p.summary is not None]
    all_refused = sum(s.refused_draws == s.endpoint_draws for s in summaries)
    return (
        f"# Run report: {run_id}\n\n"
        f"- Pairs: {len(pairs)}; draws made: {sum(s.endpoint_draws for s in summaries)}.\n"
        f"- Zero-length pairs (no draw made, every value null): {len(pairs) - len(summaries)}.\n"
        f"- Pairs with every draw refused: {all_refused}.\n"
        f"- DEM resolution: {terrain.resolution_m:g} m.\n"
    )


def _refusals(pairs: list[PairPrediction]) -> str:
    totals = dict.fromkeys(get_args(RefusalReason), 0)
    for pair in pairs:
        if pair.summary is not None:
            for reason, count in pair.summary.refusals.items():
                totals[reason] += count
    rows = [f"| {CAUSE_NAMES[reason]} | {count} |" for reason, count in totals.items()]
    return (
        "## Refused draws by cause\n\n"
        "A refused draw is dropped for every model (ADR 0006 item 11).\n\n"
        "| Cause | Draws |\n| --- | --- |\n" + "\n".join(rows) + "\n"
        f"| all causes | {sum(totals.values())} |\n"
    )


def _canopy(terrain: TerrainBuild) -> str:
    if terrain.canopy is None:
        return "## Canopy pixels without a value\n\nNo canopy snapshot in this run.\n"
    rows = [
        f"| {name} | {'no nodata value declared' if count is None else count} |"
        for name, count in terrain.canopy_nodata_by_tile.items()
    ]
    return (
        "## Canopy pixels without a value\n\n"
        "Counted as canopy 0 in the clutter rule. Per tile, in the tile's own pixels "
        "inside the bounding box of the run extent:\n\n"
        "| Tile | Pixels without a value |\n| --- | --- |\n" + "\n".join(rows) + "\n\n"
        f"Cells of `terrain/canopy.tif` without a value: {terrain.canopy_nodata_cells}.\n\n"
        "No corridors in this run, so no per-corridor count.\n"
    )


def _dsm_filled(pairs: list[PairPrediction]) -> str:
    medians = [
        p.summary.dsm_filled_pixels_median
        for p in pairs
        if p.summary is not None and p.summary.dsm_filled_pixels_median is not None
    ]
    if not medians:
        return "## DSM gaps filled\n\nNo pair read a DSM through P.1812's 10 m cells.\n"
    return (
        "## DSM gaps filled\n\n"
        "DSM pixels without a value, filled with clutter 0, in the 10 m cells each draw's "
        f"P.1812 profile read. Median over {len(medians)} pairs of each pair's median over its "
        f"draws: {float(np.median(medians)):g}; largest pair median: {max(medians):g}.\n"
    )


def _hata_band(pairs: list[PairPrediction]) -> str:
    shares = [p.mean_building_share for p in pairs if p.mean_building_share is not None]
    if not shares:
        return "## Okumura-Hata threshold\n\nNo buildings snapshot in this run.\n"
    low, high = THRESHOLD_BAND
    near = sum(low <= share < high for share in shares)
    return (
        "## Okumura-Hata threshold\n\n"
        f"Pairs whose variant would change if the threshold of 0.20 moved anywhere "
        f"between {low:.2f} and {high:.2f} (mean building share in [{low:.2f}, {high:.2f})): "
        f"{near} of {len(shares)}.\n"
    )
