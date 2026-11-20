"""Real site and receiver layers for an engine run on one USGS 1 m tile, from a capture snapshot.

`python -m ingester.engine_inputs` reads a finished capture snapshot (checked
against its manifest), finds its pairs under the contract's link-hour rules on
the merged snapshot (`ingester.link_rules`, `ingester.link_pairs`), selects
the tile's nodes (`ingester.tile_selection`) and writes two GeoParquet layers
in EPSG:26915 with the columns the lead agreed for the x72y429 run (issue
#44): `tx_sites_real.parquet` (`site_id`, `geometry`, `box_south_i`,
`box_north_i`, `box_west_i`, `box_east_i`, `role`) and `rx_real.parquet`
(`rx_id`, `geometry` and the same four edges). Each row carries the `lnk_02`
box its pairs' counted receipts used, as its four half-open edges in 1e-7
degrees, int64, `LinkBox`'s edges unchanged, and its point is the box centre
`terrain.boxes.EdgeBox.centre_xy_m` gives, which the engine checks to 1 mm
(ADR 0008). There is no `box_half_width_m` column. Both files carry the
key `mesh_terrain:positions` with the value `real` in their Parquet metadata
(Casey, issue #44, 4 (a)); the engine admits a `real` layer only when a
`prepared.json` beside it, in engine.prepare's format, lists its current
sha256 (`engine.prepared`), which stops accidents, not deliberate bypass
(Casey, issue #55 comment 7024307502, item 2, Q1 (a)).

The sites are the in-tile gateway of every pair that touches the tile, as the
2026-11-08 counts define touching, and the receiver is the relayer of the pair
with both ends in it (Casey, issue #44, busiest pair, 2026-11-17). The
command still prints each pair's link-hours and receipts for the local
record; no busiest-pair ranking selects anything.

Each pair carries the box its counted receipts in kept hours used at each
end (Casey, issue #44, 2026-11-17; issue #55 comment 6985802330, item 2,
(b)), under `lnk_02` with the floor, the box around merged fixes, one box per
end for the hour and the half-open cell. A node is placed once per pair at
the box that pair used (Casey: "a moving node is placed per pair"), one row
per distinct box: a node with one box keeps its pseudonym as `site_id` or
`rx_id`, and a node at two or more places gets `<pseudonym>.<n>`
(`ingester.tile_selection.site_row_ids`). The command also prints the
data-quality counts the rules give: receipts dropped as a direct hop that
the gateway's NeighborInfo contradicts (Casey, PR #66, Q4 (a): "count these
drops as a data-quality count in the run report"), and link-hours dropped for
more than one box (`lnk_02`); and, for Casey in the session, the count of
kept pairs whose hours not kept show another box, with one local line per
pair (item 2).

Each pair is judged at the tile edge by its own rows (item 6). The
2026-11-08 counts check runs first over every pair, those whose kept hours
used two boxes included, a node counting as touching when any of its boxes
is in or across the tile (items 3 and 6). Then a pair is dropped when its
row straddles the tile edge, when its placed row fails the DEM edge check,
or when it used more than one box at an end (Casey, issue #44, engine_inputs
Q1 (a), as item 6 reads it; the rule in full is in
`ingester.tile_selection`). The command prints one line per end that drops a
pair, naming both ends' pseudonyms, the end and the reason. Straddle and
two-box lines print before the tile check's count checks can refuse (Casey, PR #51, (a)),
so a refusal shows which pair and end caused it; edge-check lines print after
the nodes are placed. When the both-ends pair or a gateway-only pair is
dropped it refuses (exit 2) after those lines, before anything is written.

With or without `--sites-only`, it refuses (exit 2, nothing written) a
capture snapshot whose manifest `brokers` does not list both
`mqtt.meshtastic.org` and `mqtt.meshstl.org`: only the single merged
snapshot holds every copy of a delivery, and `pos_04`'s MeshSTL flag reads
every copy in any snapshot (Casey, issue #55 comment 6985802330, item 7;
comment 7016583529, item 4; `ingester.link_pairs.require_merged`).

With `--sites-only` (Casey, 2026-11-17, engine_inputs (c)) the command writes
`tx_sites_real.parquet` alone and never `rx_real.parquet`, for the engine run
whose receivers are the synthetic grid alone (terrain-rf-eng's
`engine.prepare --no-real-receivers`). It is accepted only with
`--tile x72y429`, since corridor runs keep the strict checks; any other tile
refuses before anything is read. The 2026-11-08 counts check still runs and
prints one line, pass or every count that differs, and never refuses. The
drop lines print at the same points as without the flag, the straddle lines
naming their pair's kind since no count check can refuse first, and no
dropped pair stops the run. The candidates are the in-tile gateways of the
site rule above, a row per gateway box. Their roles follow each pair's own
rows (item 6). A candidate
is written once per box its pairs used, with the row ids of
`ingester.tile_selection.site_row_ids`, unless that row straddles the tile
edge or fails the DEM edge check
(`ingester.tile_selection.select_sites`). Each gateway left out gets a local
`left out` line with its reason. A run with no site left refuses, with its
own message when no pair touching the tile has its gateway in it. No relayer
is placed, so no receiver is chosen and only a gateway's DEM check can drop
a pair. The counts line gains the sites written and left out and states that
no real receiver was written and that relayers were not DEM-checked. Beside
the layer, through the same staging folder, it writes the local run report
`engine_inputs_report.md`: the data-quality counts line, the drop, counts
check, rule, per-gateway and left-out lines and the counts line, in the order
they printed (Casey, issue #55, 7). The report names pseudonyms, so it is
local only, like the layer; a refusal writes neither, and an existing report
is refused. A strict run writes the same report beside its two layers.

The layers are written into a staging folder inside the output folder and
moved to their names only when all are complete. A refusal exits 2 and any
other failure raises; either way the run leaves no layer, no staging folder and
no output folder it created. An existing layer or staging folder is refused and
left as it is, with or without `--sites-only`, so a sites-only run never
leaves its sites beside an earlier `rx_real.parquet`.

The layers hold real positions, precise MeshSTL ones among them, and are
never published (`pos_04`). Casey, issue #44, 2026-11-16: "Coverage outputs
built from real gateway positions inherit the rule that precise MeshSTL
positions are never published".
The command refuses an output folder inside a git checkout and never prints or
logs a coordinate. Its per-node and drop lines tie pseudonyms to roles,
link-hours, the precise flag, the DEM check and the tile edge, which narrows
where a node is, so they are read locally; only the final counts line is for
issue #44. That line keeps the pairs dropped in all and per reason, and the
nodes that fail the DEM edge check (`extent ... fail`), but under the current
count checks every drop and every failing node refuses, so these fields read
0 whenever the line prints. The command does not write the grid receivers, the
run config or any check of the paths; those are terrain-rf-eng's.
"""

import argparse
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq
import structlog

from engine.points import EDGE_COLUMNS
from ingester.link_pairs import (
    CaptureLinks,
    SelectionError,
    capture_links,
    read_capture_rows,
)
from ingester.settings import get_ingester_settings
from ingester.tile_selection import (
    FAILS_DEM,
    LAYER_EPSG,
    MORE_THAN_ONE_BOX,
    SITE_RULE,
    STRADDLES,
    AnyPair,
    DroppedPair,
    Extent,
    KindConflict,
    PlacedNode,
    Selection,
    SiteSelection,
    Tile,
    count_differences,
    counted_states,
    dem_extent,
    kind_conflicts,
    parse_tile,
    select_nodes,
    select_sites,
    site_straddle_drops,
    straddle_drops,
    tile_counts,
)
from pipeline.common.logging import configure_logging
from pipeline.common.snapshot import SnapshotError, snapshot_folder

log = structlog.get_logger(component="ingester.engine_inputs")

SITES_FILE = "tx_sites_real.parquet"
RECEIVERS_FILE = "rx_real.parquet"
STAGING_FOLDER = "engine-inputs.partial"
# Casey, issue #44, 4 (a): ingest-eng's layers "real", the grid and fixtures
# "synthetic". The lead's interface note on issue #44 (2026-11-16) adds the
# rule for both agents: a layer is "synthetic" only when every row is made up;
# any real row makes it "real".
POSITIONS_KEY = b"mesh_terrain:positions"
POSITIONS_REAL = b"real"
NEVER_PUBLISH = (
    "These layers hold real node positions, precise MeshSTL positions among them, and are "
    'never published (pos_04). Casey, issue #44, 2026-11-16: "Coverage outputs built from '
    "real gateway positions inherit the rule that precise MeshSTL positions are never "
    'published". '
    "The lines above that name pseudonyms are for local reading; only the counts line goes "
    "on issue #44."
)
# Casey, 2026-11-17, engine_inputs (c): the x72y429 run with its real sites and
# no real receiver. Corridor runs keep the strict checks (issue #44 fallback).
SITES_ONLY_TILE = "x72y429"
SITES_ONLY_RULE = (
    "sites only (Casey, 2026-11-17, engine_inputs (c)): a gateway is written once per box its "
    "pairs used under lnk_02 (Casey, issue #44, 2026-11-17: a moving node is placed per pair), "
    "unless it straddles the tile edge or the row fails the DEM edge check; no real receiver; "
    "neither a dropped pair nor the counts check stops the run"
)
# Casey, PR #66, Q4 (a): "drop the receipt, and count these drops as a data-quality
# count in the run report". The name is the one B2b gave it (PR #68).
DIRECT_HOP_CONTRADICTED = "lnk_01_direct_hop_contradicted"
ONE_BOX_DROPPED_HOURS = "one_box_dropped_hours"
# Casey, PR #70 comment 6992903491, (b): "The local report states plainly that this strict
# run produced no real receiver."
NO_RECEIVER_LINE = (
    "no real receiver: this strict run produced no real receiver and wrote no rx_real.parquet, "
    "because no pair has both ends in the tile by its own rows; engine.prepare refuses this "
    "folder unless --no-real-receivers is passed (Casey, PR #70 comment 6992903491)"
)
# Casey's answer that each pair's own row decides its kind and role.
KIND_SOURCE = "issue #55 comment 6990646633, item 3"
OTHER_BOX_COUNT = "pairs kept with another box in hours not kept:"
COUNTS_CHECK = "counts check against 2026-11-08"
# Casey, issue #55, 7: "write the counts line and every left-out gateway line
# into the local run report, not only the terminal. Nothing public."
REPORT_FILE = "engine_inputs_report.md"
REPORT_NOTE = (
    "Local only. These lines name pseudonyms and tie them to roles, the DEM edge check and "
    "the tile edge, which narrows where a node is, so this file is never committed, posted "
    "or published; only its last line, the counts line, goes on issue #44 "
    '(Casey, issue #55, 7: "Nothing public.").'
)


def data_quality_line(links: CaptureLinks) -> str:
    """The data-quality counts of the link rules: counts only, no pseudonym."""
    return (
        f"data-quality counts: {DIRECT_HOP_CONTRADICTED} {links.direct_hop_contradicted} "
        "(lnk_01, a direct hop the gateway's NeighborInfo contradicts, dropped; Casey, "
        f"PR #66, Q4 (a)); {ONE_BOX_DROPPED_HOURS} {links.one_box_dropped_hours} (lnk_02)"
    )


def other_box_lines(links: CaptureLinks) -> list[str]:
    """The count of kept pairs whose hours not kept show another box, then one line per pair.

    Casey, issue #55 comment 6985802330, item 2: such a pair "is kept but
    reported to me in the session, never on issue #44". The per-pair lines
    name pseudonyms, so they are local; only the count is a count.
    """
    lines = [
        f"{OTHER_BOX_COUNT} {len(links.other_box_pairs)} (Casey, issue #55 comment "
        "6985802330, item 2: kept, reported to Casey in the session, never on issue #44)"
    ]
    for pair in links.other_box_pairs:
        ends = " and ".join(pair.ends)
        lines.append(
            f"kept pair {pair.relayer} to {pair.gateway}: {ends} used another box in hours not kept"
        )
    return lines


def kind_conflict_line(conflict: KindConflict) -> str:
    """The local line for a pair whose kind by its own rows differs from the counts check."""
    pair = conflict.pair
    return (
        f"kind differs, pair {pair.relayer} to {pair.gateway}: {conflict.by_rows} by its own "
        f"rows, {conflict.counted} by the counts check; for Casey in the session ({KIND_SOURCE})"
    )


def _print_pairs(links: CaptureLinks) -> list[str]:
    """The pair lines, then the data-quality counts and the pairs kept with another box.

    Returns:
        The lines the local run report keeps: the data-quality line and the
        other-box lines, in order. The pairs dropped for two boxes print
        with the straddle lines, after the tile line (`straddle_drops`).
    """
    counts = links.pair_counts
    missing = links.one_coordinate_missing
    print(f"positions with one coordinate missing, not positions (pos_01): {missing}")
    print(
        "pairs with a kept link-hour under the contract, both brokers merged: "
        f"{counts['pairs']} ({counts['placed']} with one box per end, "
        f"{counts['more than one box']} with more than one box)"
    )
    for pair in links.pairs:
        print(
            f"pair {pair.relayer} to {pair.gateway}: link-hours {pair.link_hours}, with a "
            f"public receipt {pair.link_hours_public}, with a MeshSTL receipt "
            f"{pair.link_hours_meshstl}; receipts {pair.receipts}"
        )
    record: list[str] = []
    for line in (data_quality_line(links), *other_box_lines(links)):
        _say(line, record)
    return record


def _print_tile(links: CaptureLinks, tile: Tile) -> dict[str, int]:
    counts = tile_counts(counted_states(links, tile))
    parts = ", ".join(f"{label} {count}" for label, count in counts.items())
    print(f"tile {tile.name}: pairs with {parts}")
    return counts


DropEnd = tuple[AnyPair, str, str]
"""A pair, the end that drops it and the reason."""


def _say(line: str, record: list[str]) -> None:
    """Print a line and keep it for the local run report."""
    print(line)
    record.append(line)


def _print_drops(dropped: Sequence[DroppedPair], printed: set[DropEnd]) -> list[str]:
    """One line per end that drops a pair, skipping ends already printed; adds to printed.

    Returns:
        The lines printed, in order.
    """
    record: list[str] = []
    for found in dropped:
        pair = found.pair
        for end, reason in found.reasons:
            if (pair, end, reason) not in printed:
                printed.add((pair, end, reason))
                line = f"dropped {found.kind} {pair.relayer} to {pair.gateway}: {end} {reason}"
                _say(line, record)
    return record


def _print_selection(selection: Selection, printed: set[DropEnd]) -> list[str]:
    """The site rule, per placed node its DEM edge check, then the drop lines not printed yet.

    Returns:
        The lines printed, in order.
    """
    record: list[str] = []
    _say(f"sites by tile membership: {SITE_RULE}", record)
    for node in selection.placed:
        inside = selection.dem_check[node.layer_id]
        line = (
            f"{node.layer_id} {node.role}: box half-widths {node.box.half_width_ns_m:.0f} m "
            f"north-south and {node.box.half_width_ew_m:.0f} m east-west, "
            f"precise source {str(node.precise_source).lower()}, box plus 50 m inside the DEM "
            f"(engine rule): extent {'pass' if inside else 'fail'}"
        )
        _say(line, record)
    return [*record, *_print_drops(selection.dropped, printed)]


def counts_line(links: CaptureLinks, tile: dict[str, int], selection: Selection) -> str:
    """The one line for issue #44: counts only, no pseudonym.

    A pair dropped for both reasons counts once in the pairs dropped and once
    under each reason.
    """
    pairs = links.pair_counts
    verdicts = [selection.dem_check[node.layer_id] for node in selection.placed]
    reasons = [
        reason for dropped in selection.dropped for reason in {r for _, r in dropped.reasons}
    ]
    return (
        f"counts for issue #44: pairs {pairs['pairs']} (one box per end {pairs['placed']}, "
        f"more than one box {pairs['more than one box']}); pairs touching the tile "
        f"{tile['touched']} (both ends {tile['both ends']}); nodes selected {len(verdicts)}; "
        f"extent pass {sum(verdicts)}, fail {len(verdicts) - sum(verdicts)}; pairs dropped "
        f"{len(selection.dropped)} ({STRADDLES} {reasons.count(STRADDLES)}, "
        f"{FAILS_DEM} {reasons.count(FAILS_DEM)}, {MORE_THAN_ONE_BOX} "
        f"{reasons.count(MORE_THAN_ONE_BOX)})"
    )


def counts_check_line(differences: Sequence[str]) -> str:
    """The sites-only run's report of the 2026-11-08 counts check, which never refuses it."""
    if not differences:
        return f"{COUNTS_CHECK}: pass"
    found = "; ".join(differences)
    return f"{COUNTS_CHECK}: differs ({found}); --sites-only reports it and does not refuse"


def sites_only_counts_line(links: CaptureLinks, tile: dict[str, int], chosen: SiteSelection) -> str:
    """The issue #44 line of a sites-only run: counts only, no pseudonym, and no receiver."""
    kept = sum(chosen.selection.dem_check.values())
    return (
        f"{counts_line(links, tile, chosen.selection)}; sites written {kept}, left out "
        f"{len(chosen.left_out)}; no real receiver written; relayers not DEM-checked"
    )


def _inside_checkout(folder: Path) -> bool:
    resolved = folder.resolve()
    return any((parent / ".git").exists() for parent in (resolved, *resolved.parents))


def _check_targets(out: Path, report: bool = False) -> list[Path]:
    """The two layer paths in out, refusing a checkout or any file the run could overwrite.

    The files are both layers, the staging folder and, with report, the run
    report REPORT_FILE. A checkout is refused because the layers hold real
    positions and the report names pseudonyms.
    """
    if _inside_checkout(out):
        raise SelectionError(f"{out} is inside a git checkout; real positions stay out of git")
    names = (SITES_FILE, RECEIVERS_FILE, STAGING_FOLDER, *([REPORT_FILE] if report else []))
    existing = [name for name in names if (out / name).exists()]
    if existing:
        raise SelectionError(f"{', '.join(existing)} exists in {out}; nothing is overwritten")
    return [out / SITES_FILE, out / RECEIVERS_FILE]


def _frame(nodes: list[PlacedNode], id_column: str, with_role: bool) -> gpd.GeoDataFrame:
    """A layer: each node at its box centre with the box's four edges (ADR 0008)."""
    columns: dict[str, object] = {id_column: [node.layer_id for node in nodes]}
    geometry = gpd.points_from_xy(
        [node.easting_m for node in nodes], [node.northing_m for node in nodes]
    )
    for column, edge in zip(EDGE_COLUMNS, ("south_i", "north_i", "west_i", "east_i"), strict=True):
        columns[column] = pd.Series([getattr(node.box, edge) for node in nodes], dtype="int64")
    if with_role:
        columns["role"] = [node.role for node in nodes]
    frame = gpd.GeoDataFrame(columns, geometry=geometry, crs=f"EPSG:{LAYER_EPSG}")
    order = [id_column, "geometry", *EDGE_COLUMNS, *(["role"] if with_role else [])]
    return frame[order]


def _mark_real(path: Path) -> None:
    """Add the positions marker to a layer's file metadata, keeping the GeoParquet `geo` key.

    The file is still in the staging folder, so rewriting it in place changes
    no finished output.
    """
    table = pq.read_table(path)
    metadata = {**(table.schema.metadata or {}), POSITIONS_KEY: POSITIONS_REAL}
    pq.write_table(table.replace_schema_metadata(metadata), path)


def _write(path: Path, frame: gpd.GeoDataFrame) -> None:
    started = time.monotonic()
    # "x" creates the file and fails if it exists, so nothing is ever overwritten.
    with path.open("xb") as handle:
        frame.to_parquet(handle)
    _mark_real(path)
    duration_s = round(time.monotonic() - started, 3)
    log.info(
        "layer_written",
        path=str(path),
        rows=len(frame),
        bytes=path.stat().st_size,
        duration_s=duration_s,
    )


def report_text(heading: str, source: str, lines: Sequence[str]) -> str:
    """The local run report: a heading, the local-only note, the snapshots and the lines.

    Args:
        heading: The run, such as `engine_inputs --sites-only, x72y429`.
        source: The snapshots the run read, one sentence.
        lines: The terminal lines the report keeps, in the order they printed.
    """
    block = "\n".join(lines)
    return f"# {heading}\n\n{REPORT_NOTE}\n\n{source}\n\n```text\n{block}\n```\n"


def _write_report(path: Path, text: str) -> None:
    started = time.monotonic()
    # "x" creates the file and fails if it exists; newline="\n" keeps one form on every host.
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    duration_s = round(time.monotonic() - started, 3)
    log.info("report_written", path=str(path), bytes=path.stat().st_size, duration_s=duration_s)


def _missing_folders(out: Path) -> list[Path]:
    """out and each parent that does not exist yet, deepest first."""
    return [folder for folder in (out, *out.parents) if not folder.exists()]


def _remove_run_files(staging: Path | None, published: list[Path], created: list[Path]) -> None:
    """Remove what this run wrote, after a failure: its own files and folders only."""
    for path in published:
        path.unlink(missing_ok=True)
    if staging is not None and staging.is_dir():
        for path in staging.iterdir():
            path.unlink()
        staging.rmdir()
    for folder in created:
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    log.warning("run_files_removed_after_failure", published=len(published), folders=len(created))


def _publish(staging: Path, targets: list[Path], published: list[Path]) -> None:
    for target in targets:
        if target.exists():
            raise SelectionError(
                f"{target.name} appeared in {target.parent}; nothing is overwritten"
            )
        (staging / target.name).rename(target)
        published.append(target)
    staging.rmdir()


def write_layers(
    out: Path, sites: list[PlacedNode], receiver: PlacedNode | None, report: str | None = None
) -> None:
    """Write the layers, and the run report when given, into out through a staging folder.

    All or nothing: every file is moved to its name only when all are written.

    Args:
        out: The output folder; it and its missing parents are created.
        sites: The site rows, in order.
        receiver: The receiver row, or None to write the site layer alone
            (`--sites-only`).
        report: The text of REPORT_FILE, or None to write no report.

    Raises:
        SelectionError: if out is inside a git checkout, or either layer, the
            staging folder or (with report) a report exists, whether or not
            this call writes it; nothing is written.
    """
    targets = _check_targets(out, report=report is not None)
    if receiver is None:
        targets = [out / SITES_FILE]
    if report is not None:
        targets.append(out / REPORT_FILE)
    created = _missing_folders(out)
    out.mkdir(parents=True, exist_ok=True)
    staging = out / STAGING_FOLDER
    try:
        staging.mkdir()
    except FileExistsError as err:
        _remove_run_files(None, [], created)
        raise SelectionError(f"{STAGING_FOLDER} appeared in {out}; it is left as it is") from err
    published: list[Path] = []
    try:
        _write(staging / SITES_FILE, _frame(sites, "site_id", with_role=True))
        if receiver is not None:
            _write(staging / RECEIVERS_FILE, _frame([receiver], "rx_id", with_role=False))
        if report is not None:
            _write_report(staging / REPORT_FILE, report)
        _publish(staging, targets, published)
    except BaseException:
        # The exception goes on; first remove what this run wrote, so no partial
        # set of real-position files is left behind.
        _remove_run_files(staging, published, created)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--snapshot", required=True, help="the capture snapshot id")
    parser.add_argument("--tile", required=True, help="USGS 1 m tile name, such as x72y429")
    parser.add_argument("--dem-snapshot", required=True, help="the tile's DEM snapshot id")
    parser.add_argument("--out", required=True, type=Path, help="folder for the two layers")
    parser.add_argument(
        "--expect-pairs", required=True, type=int,
        help="the pairs the contract's link-hour rules give on the snapshot (pairs with a kept "
        "link-hour, those dropped for more than one box included)",
    )  # fmt: skip
    parser.add_argument("--snapshots-dir", type=Path, default=None)
    parser.add_argument(
        "--sites-only", action="store_true",
        help=f"write {SITES_FILE} alone, with no real receiver; only with --tile {SITES_ONLY_TILE}",
    )  # fmt: skip
    return parser


def _check_sites_only(args: argparse.Namespace) -> None:
    if args.sites_only and args.tile != SITES_ONLY_TILE:
        raise SelectionError(
            f"--sites-only is accepted only with --tile {SITES_ONLY_TILE} (Casey, 2026-11-17: "
            "this run only; corridor runs keep the strict checks)"
        )


@dataclass(frozen=True)
class _Run:
    """What both kinds of run share once the capture is read and the straddle lines printed."""

    out: Path
    expect_pairs: int
    links: CaptureLinks
    tile: Tile
    extent: Extent
    tile_counts: dict[str, int]
    printed: set[DropEnd]
    straddle_lines: list[str]
    source: str
    capture_lines: tuple[str, ...] = ()


def _write_both(run: _Run) -> None:
    """The strict run: the counts check, the stop on a dropped selected pair, both layers.

    Beside the layers it writes the local run report, so the lines for Casey
    in the session (the kind disagreements, the other-box and two-box pairs)
    are kept with the layers they belong to; a refusal writes neither. With
    no both-ends pair by rows it writes the sites alone and says so in a line
    of its own (Casey, PR #70 comment 6992903491, (b)).
    """
    selection = select_nodes(run.links, run.tile, run.expect_pairs, run.extent)
    record = [*run.capture_lines, *run.straddle_lines]
    record += _print_selection(selection, run.printed)
    # Casey's stop for a dropped selected pair comes here, after the drop lines
    # and before anything with real positions is written.
    sites, receiver = selection.layers()
    if receiver is None:
        _say(NO_RECEIVER_LINE, record)
    counts = counts_line(run.links, run.tile_counts, selection)
    heading = f"engine_inputs, {run.tile.name}"
    write_layers(run.out, sites, receiver, report_text(heading, run.source, [*record, counts]))
    written = f"{RECEIVERS_FILE} (1 receiver)" if receiver is not None else f"no {RECEIVERS_FILE}"
    print(f"wrote {SITES_FILE} ({len(sites)} sites), {written} and {REPORT_FILE} (local only)")
    print(counts)


def _write_sites(run: _Run) -> None:
    """The sites-only run: the counts check reported, never applied; the site layer alone.

    The report holds the data-quality, pair drop, straddle, counts check,
    selection, rule and left-out lines and the counts line, and is written
    beside the layer only when the layer is; a refusal writes neither, though
    the terminal has the lines.
    """
    record = [*run.capture_lines, *run.straddle_lines]
    states = counted_states(run.links, run.tile)
    differences = count_differences(run.links, states, run.tile, run.expect_pairs)
    _say(counts_check_line(differences), record)
    chosen = select_sites(run.links, run.tile, run.extent)
    record += _print_selection(chosen.selection, run.printed)
    _say(SITES_ONLY_RULE, record)
    for site in chosen.left_out:
        _say(f"left out {site.layer_id} {site.role}: {site.reason}", record)
    sites = chosen.sites()
    counts = sites_only_counts_line(run.links, run.tile_counts, chosen)
    heading = f"engine_inputs --sites-only, {run.tile.name}"
    write_layers(run.out, sites, None, report_text(heading, run.source, [*record, counts]))
    print(
        f"wrote {SITES_FILE} ({len(sites)} sites) and {REPORT_FILE} (local only); "
        f"no {RECEIVERS_FILE}: no real receiver written (--sites-only)"
    )
    print(counts)


def _run(args: argparse.Namespace) -> None:
    _check_sites_only(args)
    tile = parse_tile(args.tile)
    _check_targets(args.out, report=True)
    root = args.snapshots_dir or get_ingester_settings().snapshots_dir
    extent = dem_extent(root, args.dem_snapshot)
    rows = read_capture_rows(snapshot_folder(root, args.snapshot))
    print(f"snapshot {args.snapshot}: {len(rows)} delivery rows, every file matched its manifest")
    links = capture_links(rows)
    capture_lines = _print_pairs(links)
    counts = _print_tile(links, tile)
    # Casey, PR #51, (a): straddle drop lines print before the count checks
    # in select_nodes can refuse, so a refusal shows which pair and end caused it.
    # A sites-only run never refuses on the counts, so its lines name the kind.
    printed: set[DropEnd] = set()
    if args.sites_only:
        straddles = _print_drops(site_straddle_drops(links, tile), printed)
    else:
        straddles = _print_drops(straddle_drops(links, tile, args.expect_pairs), printed)
    # Casey (issue #55 comment 6990646633, item 3): a pair whose kind by its own
    # rows differs from the counts check is listed for him and does not stop the run.
    for conflict in kind_conflicts(links, tile):
        _say(kind_conflict_line(conflict), straddles)
    source = f"Snapshot {args.snapshot}, DEM snapshot {args.dem_snapshot}."
    run = _Run(
        args.out, args.expect_pairs, links, tile, extent, counts, printed, straddles, source,
        tuple(capture_lines),
    )  # fmt: skip
    (_write_sites if args.sites_only else _write_both)(run)
    print(NEVER_PUBLISH)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command.

    Args:
        argv: Command-line arguments; sys.argv when None.

    Returns:
        0 when the layers were written (the site layer alone with
        `--sites-only`), 2 when the run was refused and wrote nothing.
    """
    args = _parser().parse_args(argv)
    try:
        _run(args)
    except (SelectionError, SnapshotError) as err:
        log.error("engine_inputs_refused", reason=str(err))
        print(f"refused, nothing written: {err}")
        print("on issue #44, post only that the command refused (exit 2); the reason is for Casey")
        return 2
    return 0


if __name__ == "__main__":
    configure_logging("console")
    sys.exit(main())
