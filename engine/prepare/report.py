"""`crossing.md`: the crossing check written for Casey, by pseudonym and count only.

Paths between real endpoints are listed one by one with the site's role,
yes or no and the distance from the path to the nearest tall cell, or the
section says the run has no real receiver; grid paths are counted per site.
The file holds no coordinate of any node, real or made up. Box half-widths
are listed because the engine's draws move each end inside its box; they
come from position precision, so the file and the printed summary are read
locally only and never posted, and issue #44 gets counts only (the lead's
rule; the repository is public).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from engine.prepare.crossing import PathCrossing
from engine.prepare.run_config import NEVER_PUBLISHED
from engine.snapshots import NamedSnapshot

PRIVACY = (
    f"Coverage outputs built from real gateway positions {NEVER_PUBLISHED} (Casey, issue #44). "
    "This file names nodes by pseudonym only and holds no coordinates; it stays in this "
    "folder, outside git. This file and the printed summary are read locally only and never "
    "posted: the repository is public, so issue #44 gets counts only."
)


@dataclass(frozen=True, kw_only=True)
class Site:
    """A real site as the report shows it.

    Attributes:
        site_id: Pseudonym.
        role: `both_ends_gateway` or `gateway_only_gateway`.
        half_width_m: Position box half-width in metres.
        in_run: Whether its box plus 50 m lies inside the DEM.
    """

    site_id: str
    role: str
    half_width_m: float
    in_run: bool


@dataclass(frozen=True, kw_only=True)
class CrossingReport:
    """Everything `crossing.md` says.

    Attributes:
        tile: The tile name.
        dem: The DEM snapshot read.
        dsm: The DSM snapshot read.
        threshold_m: DSM minus DEM a tall cell exceeds, metres.
        within_m: Crossing distance, metres.
        tall_cells: Number of tall cells found.
        sites: Every real site, in or out of the run.
        receiver_half_widths: Real receiver pseudonym to box half-width, metres;
            empty for a run whose receivers are the grid alone.
        real: Crossings of the paths from sites in the run to real receivers.
        grid: Crossings of the paths from sites in the run to grid receivers.
    """

    tile: str
    dem: NamedSnapshot
    dsm: NamedSnapshot
    threshold_m: float
    within_m: float
    tall_cells: int
    sites: Sequence[Site]
    receiver_half_widths: dict[str, float]
    real: Sequence[PathCrossing]
    grid: Sequence[PathCrossing]


def crossing_markdown(report: CrossingReport) -> str:
    """The Markdown text of `crossing.md`."""
    lines = [
        f"# Crossing check, {report.tile}",
        "",
        PRIVACY,
        "",
        f"A tall cell is a DSM cell more than {report.threshold_m:g} m above the DEM at the "
        f"same place. A path crosses when the straight line between its site's and its "
        f"receiver's box centres passes within {report.within_m:g} m of a tall cell's square. "
        "The engine draws each end anywhere inside its box, so a drawn path can lie up to the "
        "larger box half-width either side of the centre line.",
        "",
        "| DEM snapshot | DSM snapshot | tall cells |",
        "| --- | --- | --- |",
        f"| {_snapshot(report.dem)} | {_snapshot(report.dsm)} | {report.tall_cells} |",
        "",
        *_site_lines(report),
        "",
        *_real_lines(report),
        "",
        *_grid_lines(report),
    ]
    return "\n".join(lines) + "\n"


def _snapshot(snapshot: NamedSnapshot) -> str:
    return f"`{snapshot.snapshot_id}` (manifest sha256 `{snapshot.manifest_sha256}`)"


def _site_lines(report: CrossingReport) -> list[str]:
    rows = [
        f"| {s.site_id} | {s.role} | {'in the run' if s.in_run else 'left out'} |"
        for s in report.sites
    ]
    return [
        "## Sites",
        "",
        "A site left out has a box that, with the engine's 50 m margin, reaches outside the "
        "DEM snapshot; it is not in `tx_sites.parquet`.",
        "",
        "| site_id | role | extent check |",
        "| --- | --- | --- |",
        *rows,
    ]


def _real_lines(report: CrossingReport) -> list[str]:
    if not report.receiver_half_widths:
        return [
            "## Paths between real endpoints",
            "",
            "This run has no real receiver (`--no-real-receivers`): its receivers are the "
            "made-up grid alone, so there is no path between real endpoints.",
        ]
    sites = {s.site_id: s for s in report.sites}
    rows = []
    for c in report.real:
        site = sites[c.site_id]
        half_width_m = max(site.half_width_m, report.receiver_half_widths[c.rx_id])
        rows.append(
            f"| {c.site_id} | {site.role} | {c.rx_id} | {_yes_no(c)} | {_distance(c.distance_m)} "
            f"| {half_width_m:.1f} |"
        )
    return [
        "## Paths between real endpoints",
        "",
        f"| site_id | role | rx_id | within {report.within_m:g} m | closest distance (m) "
        "| larger box half-width (m) |",
        "| --- | --- | --- | --- | --- | --- |",
        *rows,
    ]


def _grid_lines(report: CrossingReport) -> list[str]:
    rows = []
    for site in (s for s in report.sites if s.in_run):
        paths = [c for c in report.grid if c.site_id == site.site_id]
        crossing = sum(c.crosses for c in paths)
        known = [c.distance_m for c in paths if c.distance_m is not None]
        closest = _distance(min(known) if known else None)
        rows.append(f"| {site.site_id} | {len(paths)} | {crossing} | {closest} |")
    return [
        "## Grid paths",
        "",
        "Paths from each site in the run to the made-up grid receivers, counted.",
        "",
        f"| site_id | grid paths | within {report.within_m:g} m | closest distance (m) |",
        "| --- | --- | --- | --- |",
        *rows,
    ]


def _yes_no(crossing: PathCrossing) -> str:
    return "yes" if crossing.crosses else "no"


def _distance(distance_m: float | None) -> str:
    return "no tall cell" if distance_m is None else f"{distance_m:.1f}"
