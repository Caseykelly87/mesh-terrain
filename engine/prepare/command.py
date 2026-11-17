"""The command `python -m engine.prepare <tile> --folder <folder>`.

It reads the two real layers another step wrote into the folder
(`tx_sites_real.parquet`, `rx_real.parquet`, EPSG:26915), and in this order:

1. refuses a folder inside a git checkout, a folder that already holds one of
   its outputs, and layers without the agreed columns;
2. checks every site and receiver box plus 50 m against the DEM snapshot's
   extent with the engine's own rule: a receiver that fails stops the
   command, a site that fails is left out and reported;
3. finds the DSM cells more than 60 m above the DEM and measures every run
   path's distance to them;
4. writes `rx_points.parquet` (the real receivers, then the grid),
   `tx_sites.parquet` (the sites in the run), `crossing.md` and
   `<tile>.yaml` into a new temporary folder inside the folder, stamps the
   two point layers with the positions marker (`real` unless every input
   row is marked `synthetic`; `engine.positions_marker`), checks the
   config with the engine's own `load_config`, then moves each file to its
   name, and prints `crossing.md`.

With `--no-real-receivers` it reads only `tx_sites_real.parquet` and refuses
a folder that holds `rx_real.parquet`, so a folder prepared for a run with a
real receiver never loses it silently. The receivers are then the grid
alone, `rx_points.parquet` is stamped `synthetic`, and `crossing.md` says the
run has no real receiver; every other step is as above.

Nothing is written under an output's name unless every check passes, and a
failure at any step, the moves included, leaves the folder as it was: the
temporary folder and any output already moved are removed. Exit status 0
when the folder is written, 2 when anything was refused, 1 for a failure
while writing. No log line or message holds a coordinate; refusals name
endpoints by id.
"""

import argparse
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from shutil import rmtree
from typing import Any

import geopandas as gpd
import pyarrow.parquet as pq
import structlog
from pydantic import ValidationError
from shapely.geometry import Point

from engine.config import SnapshotIds, load_config
from engine.errors import ConfigError
from engine.points import Endpoint, read_points
from engine.positions_marker import (
    POSITIONS_KEY,
    REAL,
    SYNTHETIC,
    positions_marker,
    stamp_positions,
)
from engine.prepare.crossing import CROSSING_DISTANCE_M, TALL_CELL_M, path_crossings, tall_cells
from engine.prepare.extent import outside_extent
from engine.prepare.report import CrossingReport, Site, crossing_markdown
from engine.prepare.run_config import (
    RECEIVERS_FILE,
    SITES_FILE,
    TILE_SNAPSHOTS,
    config_name,
    config_text,
)
from engine.prepare.tile import Tile, grid_receivers, parse_tile
from engine.run import SITE_ID
from engine.settings import EngineSettings
from engine.snapshots import NamedSnapshot, resolve_snapshots
from pipeline.common.logging import configure_logging

log = structlog.get_logger(component="engine.prepare")

REAL_SITES_FILE = "tx_sites_real.parquet"
REAL_RECEIVERS_FILE = "rx_real.parquet"
CROSSING_FILE = "crossing.md"
SITE_COLUMNS = ("site_id", "geometry", "box_half_width_m", "role")
RECEIVER_COLUMNS = ("rx_id", "geometry", "box_half_width_m")
SITE_ROLES = ("both_ends_gateway", "gateway_only_gateway")
LAYER_CRS = "EPSG:26915"


def main(argv: list[str] | None = None) -> int:
    """Parse the command line and prepare one tile run's folder.

    Args:
        argv: Arguments after the program name; the process's by default.

    Returns:
        The exit status.
    """
    parser = argparse.ArgumentParser(prog="python -m engine.prepare")
    parser.add_argument("tile", help="USGS 1 m tile name with a defined run, such as x72y429")
    parser.add_argument("--folder", type=Path, required=True, help="the run's config folder")
    parser.add_argument(
        "--no-real-receivers",
        action="store_true",
        help="the grid alone as receivers; refuses a folder that holds rx_real.parquet",
    )
    arguments = parser.parse_args(argv)
    try:
        settings = EngineSettings()
    except ValidationError as err:
        configure_logging("console")
        missing = ", ".join(str(e["loc"][0]).upper() for e in err.errors())
        log.error("prepare_settings_refused", reason=f"set {missing} in the environment")
        return 2
    configure_logging(settings.log_format)
    try:
        summary = prepare_folder(
            arguments.tile,
            arguments.folder,
            settings.snapshots_dir,
            real_receivers=not arguments.no_real_receivers,
        )
    except ConfigError as err:
        log.error("prepare_refused", tile=arguments.tile, reason=str(err))
        return 2
    sys.stdout.write(summary)
    return 0


def prepare_folder(
    tile_name: str, folder: Path, snapshots_dir: Path, *, real_receivers: bool = True
) -> str:
    """Check the folder's real layers and write the tile run's inputs and config into it.

    Args:
        tile_name: A tile with a defined run, such as x72y429.
        folder: The run's config folder, holding the two real layers.
        snapshots_dir: The snapshots folder (`SNAPSHOTS_DIR`).
        real_receivers: False for a run whose receivers are the grid alone;
            the folder must then hold no `rx_real.parquet`.

    Returns:
        The text of `crossing.md`.

    Raises:
        ConfigError: for any refusal; nothing is written then.
    """
    tile = _tile(tile_name)
    _check_folder(folder, tile, real_receivers)
    log.info(
        "prepare_layers_read_begin",
        tile=tile.name,
        folder=str(folder),
        real_receivers=real_receivers,
    )
    sites, roles = _read_sites(folder / REAL_SITES_FILE)
    receivers, receivers_marker = _read_receivers(folder, real_receivers)
    markers = {
        SITES_FILE: _input_marker(folder / REAL_SITES_FILE),
        RECEIVERS_FILE: receivers_marker,
    }
    log.info("prepare_layers_read_end", sites=len(sites), receivers=len(receivers), **markers)
    grid = grid_receivers(tile)
    _check_unique([r.endpoint_id for r in receivers + grid])
    snapshots = _snapshots(tile, snapshots_dir)
    in_run = _sites_in_run(sites, receivers, grid, snapshots["dem"])
    report = _crossing_report(tile, snapshots, sites, roles, in_run, receivers, grid)
    summary = crossing_markdown(report)
    _write_outputs(folder, tile, in_run, receivers + grid, summary, markers)
    return summary


def _tile(name: str) -> Tile:
    try:
        tile = parse_tile(name)
    except ValueError as err:
        raise ConfigError(str(err)) from err
    if tile.name not in TILE_SNAPSHOTS:
        raise ConfigError(
            f"no run is defined for tile {tile.name}; known: {sorted(TILE_SNAPSHOTS)}"
        )
    return tile


def _outputs(folder: Path, tile: Tile) -> list[Path]:
    names = (RECEIVERS_FILE, SITES_FILE, CROSSING_FILE, config_name(tile.name))
    return [folder / name for name in names]


def _check_folder(folder: Path, tile: Tile, real_receivers: bool) -> None:
    """Refuse a folder git could pick up or this command wrote into, or a real receiver to drop."""
    if not folder.is_dir():
        raise ConfigError(f"no folder at {folder}; the real layers are written there first")
    for place in (folder.resolve(), *folder.resolve().parents):
        if (place / ".git").exists():
            raise ConfigError(
                f"{folder} lies inside the git checkout {place}; real positions stay outside git"
            )
    existing = [path.name for path in _outputs(folder, tile) if path.exists()]
    if existing:
        raise ConfigError(
            f"{folder} already holds {', '.join(existing)}; this command never overwrites"
        )
    if not real_receivers and (folder / REAL_RECEIVERS_FILE).exists():
        raise ConfigError(
            f"{folder} holds {REAL_RECEIVERS_FILE}, but --no-real-receivers takes the grid "
            "alone as receivers and would leave that real receiver out of the run"
        )


def _agreed_layer(path: Path, columns: Sequence[str]) -> Path:
    """The path of a real layer that exists and has the columns agreed with ingest-eng."""
    if not path.is_file():
        raise ConfigError(f"no layer at {path}")
    names = pq.read_schema(path).names
    missing = [name for name in columns if name not in names]
    if missing:
        raise ConfigError(f"{path.name} lacks the agreed column {', '.join(missing)}")
    return path


def _read_receivers(folder: Path, real_receivers: bool) -> tuple[list[Endpoint], str]:
    """The real receivers and the marker their layer gives `rx_points.parquet`.

    Without real receivers there is no layer to read, and the grid that
    makes up `rx_points.parquet` is made up, so it is `synthetic`.
    """
    if not real_receivers:
        return [], SYNTHETIC
    path = _agreed_layer(folder / REAL_RECEIVERS_FILE, RECEIVER_COLUMNS)
    return read_points(path, "rx_id"), _input_marker(path)


def _read_sites(path: Path) -> tuple[list[Endpoint], dict[str, str]]:
    """The real sites and each one's role, refusing a role or id the run cannot take."""
    sites = read_points(_agreed_layer(path, SITE_COLUMNS), "site_id")
    table = pq.read_table(path, columns=["site_id", "role"]).to_pydict()
    roles = dict(zip(table["site_id"], table["role"], strict=True))
    for site in sites:
        role = roles[site.endpoint_id]
        if role not in SITE_ROLES:
            raise ConfigError(
                f"{path.name}: site {site.endpoint_id} has role {role!r}, not {SITE_ROLES}"
            )
        if not SITE_ID.fullmatch(site.endpoint_id):
            raise ConfigError(
                f"{path.name}: site_id {site.endpoint_id!r} cannot name a coverage file"
            )
    return sites, roles


def _input_marker(path: Path) -> str:
    """The positions marker the outputs from this layer carry: unmarked counts as real.

    The grid is made up, so a receiver layer stays `synthetic` only when the
    real receivers' file says every one of its rows is.
    """
    marker = positions_marker(path)
    if marker is None:
        return REAL
    if marker not in (REAL, SYNTHETIC):
        raise ConfigError(f"{path.name}: {POSITIONS_KEY} is {marker!r}, not {REAL} or {SYNTHETIC}")
    return marker


def _check_unique(rx_ids: list[str]) -> None:
    repeated = sorted(rx_id for rx_id, count in Counter(rx_ids).items() if count > 1)
    if repeated:
        raise ConfigError(f"receiver ids repeat: {', '.join(repeated)}")


def _snapshots(tile: Tile, snapshots_dir: Path) -> dict[str, NamedSnapshot]:
    """The tile's DEM and DSM, each checked against its manifest as the engine checks them."""
    ids = TILE_SNAPSHOTS[tile.name]
    named = SnapshotIds(
        dem=ids["dem"], dsm=ids["dsm"], canopy=None, buildings=None, p1812_maps=None
    )
    return resolve_snapshots(named, snapshots_dir)


def _sites_in_run(
    sites: list[Endpoint], receivers: list[Endpoint], grid: list[Endpoint], dem: NamedSnapshot
) -> list[Endpoint]:
    """The sites the engine's extent rule accepts; refuse when a receiver or every site fails."""
    failing = set(outside_extent(sites + receivers + grid, dem))
    failing_receivers = [r.endpoint_id for r in receivers + grid if r.endpoint_id in failing]
    if failing_receivers:
        raise ConfigError(
            f"receiver {', '.join(failing_receivers)}: its box plus 50 m reaches outside the DEM "
            f"snapshot {dem.snapshot_id}, so the engine would refuse the run for extent"
        )
    in_run = [s for s in sites if s.endpoint_id not in failing]
    for site in sites:
        if site.endpoint_id in failing:
            # Safe to continue: the run simply has one site fewer, and
            # crossing.md lists it as left out.
            log.warning("prepare_site_left_out", site_id=site.endpoint_id, dem=dem.snapshot_id)
    log.info(
        "prepare_extent_checked",
        sites=len(sites),
        sites_in_run=len(in_run),
        receivers=len(receivers) + len(grid),
    )
    if not in_run:
        raise ConfigError(f"no site's box plus 50 m lies inside the DEM snapshot {dem.snapshot_id}")
    return in_run


def _crossing_report(
    tile: Tile,
    snapshots: dict[str, NamedSnapshot],
    sites: list[Endpoint],
    roles: dict[str, str],
    in_run: list[Endpoint],
    receivers: list[Endpoint],
    grid: list[Endpoint],
) -> CrossingReport:
    cells = tall_cells(snapshots["dem"], snapshots["dsm"], TALL_CELL_M)
    run_ids = {s.endpoint_id for s in in_run}
    return CrossingReport(
        tile=tile.name,
        dem=snapshots["dem"],
        dsm=snapshots["dsm"],
        threshold_m=TALL_CELL_M,
        within_m=CROSSING_DISTANCE_M,
        tall_cells=len(cells),
        sites=[
            Site(
                site_id=s.endpoint_id,
                role=roles[s.endpoint_id],
                half_width_m=s.box.half_width_m,
                in_run=s.endpoint_id in run_ids,
            )
            for s in sites
        ],
        receiver_half_widths={r.endpoint_id: r.box.half_width_m for r in receivers},
        real=path_crossings(in_run, receivers, cells),
        grid=path_crossings(in_run, grid, cells),
    )


def _layer(id_column: str, endpoints: list[Endpoint]) -> gpd.GeoDataFrame:
    """A point layer in the column order the engine's INTERFACE lists."""
    layer = gpd.GeoDataFrame(
        {
            id_column: [e.endpoint_id for e in endpoints],
            "box_half_width_m": [float(e.box.half_width_m) for e in endpoints],
        },
        geometry=[Point(e.box.centre_xy_m) for e in endpoints],
        crs=LAYER_CRS,
    )
    return layer[[id_column, "geometry", "box_half_width_m"]]


def move_into_place(source: Path, target: Path) -> None:
    """Move one written file to its final name, refusing to replace an existing file."""
    if target.exists():
        raise FileExistsError(f"{target} appeared while the outputs were written")
    source.rename(target)


def _write_outputs(
    folder: Path,
    tile: Tile,
    sites: list[Endpoint],
    receivers: list[Endpoint],
    summary: str,
    markers: dict[str, str],
) -> None:
    """Write the four outputs in a temporary folder, check the config, then move them in."""
    started = time.monotonic()
    log.info("prepare_write_begin", tile=tile.name, folder=str(folder))
    staging = Path(tempfile.mkdtemp(prefix=".prepare-", dir=folder))
    moved: list[Path] = []
    try:
        names = _write_staged(staging, tile, sites, receivers, summary, markers)
        for name in names:
            move_into_place(staging / name, folder / name)
            moved.append(folder / name)
    except BaseException:
        # Only files this call wrote are removed, so the folder is as it was.
        for path in moved:
            _remove_output(path)
        raise
    finally:
        rmtree(staging, onexc=_staging_left)
    log.info(
        "prepare_written",
        tile=tile.name,
        folder=str(folder),
        sites=len(sites),
        receivers=len(receivers),
        duration_s=round(time.monotonic() - started, 3),
    )


def _remove_output(path: Path) -> None:
    """Remove an output moved in before the failure; name it in a warning if that fails."""
    try:
        path.unlink(missing_ok=True)
    except OSError as err:
        _staging_left(Path.unlink, path, err)


def _staging_left(function: Any, path: str | Path, error: BaseException) -> None:
    """Name a file this command wrote but could not remove; the original error goes on.

    Safe to continue: the error that stopped the write is still raised. The
    file may hold real positions, so the warning names it for removal by hand;
    it lies in the config folder, outside git.
    """
    log.warning("prepare_staging_left", path=str(path), reason=f"{type(error).__name__}: {error}")


def _write_staged(
    staging: Path,
    tile: Tile,
    sites: list[Endpoint],
    receivers: list[Endpoint],
    summary: str,
    markers: dict[str, str],
) -> list[str]:
    """Write every output into the empty staging folder; return their names, config last."""
    _layer("rx_id", receivers).to_parquet(staging / RECEIVERS_FILE, index=False)
    _layer("site_id", sites).to_parquet(staging / SITES_FILE, index=False)
    for name, marker in markers.items():
        stamp_positions(staging / name, marker)
    with (staging / CROSSING_FILE).open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(summary)
    config = staging / config_name(tile.name)
    with config.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(config_text(tile.name))
    try:
        load_config(config)
    except ConfigError as err:
        # Every check above passed, so a config the engine refuses is a bug here,
        # a failure (exit 1) rather than a refusal of the caller's input.
        raise RuntimeError(f"the written config fails the engine's check: {err}") from err
    return [RECEIVERS_FILE, SITES_FILE, CROSSING_FILE, config.name]
