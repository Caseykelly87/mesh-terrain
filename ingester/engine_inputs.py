"""Real site and receiver layers for an engine run on one USGS 1 m tile, from a capture snapshot.

`python -m ingester.engine_inputs` reads a finished capture snapshot (checked
against its manifest), finds the pairs positioned at both ends by the ADR 0003
spike's rules (`ingester.link_pairs`), selects the tile's nodes
(`ingester.tile_selection`) and writes two GeoParquet layers in EPSG:26915
with the columns the lead agreed for the x72y429 run (issue #44):
`tx_sites_real.parquet` (`site_id`, `geometry`, `box_half_width_m`, `role`)
and `rx_real.parquet` (`rx_id`, `geometry`, `box_half_width_m`), each node at
its stationary box centre. Both files carry the key `mesh_terrain:positions`
with the value `real` in their Parquet metadata (Casey, issue #44, 4 (a)); the
engine does not read it yet.

Both layers are written into a staging folder inside the output folder and
moved to their names only when both are complete. A refusal exits 2 and any
other failure raises; either way the run leaves no layer, no staging folder and
no output folder it created. An existing layer or staging folder is refused and
left as it is.

The layers hold real positions, precise MeshSTL ones among them, and are
never published (`pos_04`). Casey, issue #44, 2026-11-16: "Coverage outputs
built from real gateway positions inherit the rule that precise MeshSTL
positions are never published".
The command refuses an output folder inside a git checkout and never prints or
logs a coordinate. Its per-node lines tie pseudonyms to roles, link-hours, the
precise flag and the DEM check, which narrows where a node is, so they are read
locally; only the final counts line is for issue #44. It does not write the
grid receivers, the run config or any check of the paths; those are
terrain-rf-eng's.
"""

import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import geopandas as gpd
import pyarrow.parquet as pq
import structlog

from ingester.link_pairs import CaptureLinks, SelectionError, capture_links, read_capture_rows
from ingester.settings import get_ingester_settings
from ingester.tile_selection import (
    BUSIEST_RULE,
    FAILS_DEM,
    LAYER_EPSG,
    STRADDLES,
    PlacedNode,
    Selection,
    Tile,
    dem_extent,
    pair_states,
    parse_tile,
    select_nodes,
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


def _print_pairs(links: CaptureLinks) -> None:
    counts = links.pair_counts
    missing = links.one_coordinate_missing
    print(f"positions with one coordinate missing, not positions (pos_01): {missing}")
    print(
        "pairs positioned at both ends, stationarity rule, 500 m: "
        f"public {counts['public']}, MeshSTL {counts['meshstl']}, "
        f"shared {counts['shared']}, union {counts['union']}"
    )
    for pair in links.pairs:
        print(
            f"pair {pair.relayer} to {pair.gateway}: link-hours over both runs "
            f"{pair.link_hours_union}, public {pair.link_hours_public}, MeshSTL "
            f"{pair.link_hours_meshstl}; receipts {pair.receipts}"
        )


def _print_tile(links: CaptureLinks, tile: Tile) -> dict[str, int]:
    counts = tile_counts(pair_states(links, tile))
    parts = ", ".join(f"{label} {count}" for label, count in counts.items())
    print(f"tile {tile.name}: pairs with {parts}")
    return counts


def _print_selection(selection: Selection) -> None:
    """Per placed node its DEM edge check, then one line per end that drops a pair."""
    print(f"busiest two pairs: {BUSIEST_RULE}")
    for node in selection.placed:
        inside = selection.dem_check[node.pseudonym]
        print(
            f"{node.pseudonym} {node.role}: box half-width {node.half_width_m:.1f} m, "
            f"precise source {str(node.precise_source).lower()}, box plus 50 m inside the DEM "
            f"(engine rule): extent {'pass' if inside else 'fail'}"
        )
    for dropped in selection.dropped:
        pair = dropped.pair
        for end, reason in dropped.reasons:
            print(f"dropped {dropped.kind} {pair.relayer} to {pair.gateway}: {end} {reason}")


def counts_line(links: CaptureLinks, tile: dict[str, int], selection: Selection) -> str:
    """The one line for issue #44: counts only, no pseudonym.

    A pair dropped for both reasons counts once in the pairs dropped and once
    under each reason.
    """
    pairs = links.pair_counts
    verdicts = [selection.dem_check[node.pseudonym] for node in selection.placed]
    reasons = [
        reason for dropped in selection.dropped for reason in {r for _, r in dropped.reasons}
    ]
    return (
        f"counts for issue #44: pairs public {pairs['public']}, MeshSTL {pairs['meshstl']}, "
        f"shared {pairs['shared']}, union {pairs['union']}; pairs touching the tile "
        f"{tile['touched']} (both ends {tile['both ends']}); nodes selected {len(verdicts)}; "
        f"extent pass {sum(verdicts)}, fail {len(verdicts) - sum(verdicts)}; pairs dropped "
        f"{len(selection.dropped)} ({STRADDLES} {reasons.count(STRADDLES)}, "
        f"{FAILS_DEM} {reasons.count(FAILS_DEM)})"
    )


def _inside_checkout(folder: Path) -> bool:
    resolved = folder.resolve()
    return any((parent / ".git").exists() for parent in (resolved, *resolved.parents))


def _check_targets(out: Path) -> list[Path]:
    if _inside_checkout(out):
        raise SelectionError(f"{out} is inside a git checkout; real positions stay out of git")
    names = (SITES_FILE, RECEIVERS_FILE, STAGING_FOLDER)
    existing = [name for name in names if (out / name).exists()]
    if existing:
        raise SelectionError(f"{', '.join(existing)} exists in {out}; nothing is overwritten")
    return [out / SITES_FILE, out / RECEIVERS_FILE]


def _frame(nodes: list[PlacedNode], id_column: str, with_role: bool) -> gpd.GeoDataFrame:
    columns: dict[str, list[object]] = {id_column: [node.pseudonym for node in nodes]}
    geometry = gpd.points_from_xy(
        [node.easting_m for node in nodes], [node.northing_m for node in nodes]
    )
    columns["box_half_width_m"] = [float(node.half_width_m) for node in nodes]
    if with_role:
        columns["role"] = [node.role for node in nodes]
    frame = gpd.GeoDataFrame(columns, geometry=geometry, crs=f"EPSG:{LAYER_EPSG}")
    order = [id_column, "geometry", "box_half_width_m", *(["role"] if with_role else [])]
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


def write_layers(out: Path, sites: list[PlacedNode], receiver: PlacedNode) -> None:
    """Write the two layers into out through a staging folder, all or nothing.

    Args:
        out: The output folder; it and its missing parents are created.
        sites: The site rows, in order.
        receiver: The receiver row.

    Raises:
        SelectionError: if out is inside a git checkout, or a layer or the
            staging folder exists; nothing is written.
    """
    targets = _check_targets(out)
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
        _write(staging / RECEIVERS_FILE, _frame([receiver], "rx_id", with_role=False))
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
        help="the union of both-ends pairs the tile check found (8 on 2026-11-08)",
    )  # fmt: skip
    parser.add_argument("--snapshots-dir", type=Path, default=None)
    return parser


def _run(args: argparse.Namespace) -> None:
    tile = parse_tile(args.tile)
    _check_targets(args.out)
    root = args.snapshots_dir or get_ingester_settings().snapshots_dir
    extent = dem_extent(root, args.dem_snapshot)
    rows = read_capture_rows(snapshot_folder(root, args.snapshot))
    print(f"snapshot {args.snapshot}: {len(rows)} delivery rows, every file matched its manifest")
    links = capture_links(rows)
    _print_pairs(links)
    counts = _print_tile(links, tile)
    selection = select_nodes(links, tile, args.expect_pairs, extent)
    _print_selection(selection)
    # Casey's stop for a dropped selected pair comes here, after the drop lines
    # and before anything with real positions is written.
    sites, receiver = selection.layers()
    write_layers(args.out, sites, receiver)
    print(f"wrote {SITES_FILE} ({len(sites)} sites) and {RECEIVERS_FILE} (1 receiver)")
    print(counts_line(links, counts, selection))
    print(NEVER_PUBLISH)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command.

    Args:
        argv: Command-line arguments; sys.argv when None.

    Returns:
        0 when both layers were written, 2 when the run was refused and wrote nothing.
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
