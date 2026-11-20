"""One engine run: check everything, write into `<run_id>.partial/`, manifest last, rename.

The order is the contract of `docs/INTERFACE.md`:

1. Check, writing nothing: the config and profiles, every named snapshot
   against its manifest, the point layers (each `synthetic`, or `real` and
   written by engine.prepare, `engine.prepared`), the observations, the run grid
   against the DEM, the code provenance, that neither `<run_id>/` nor
   `<run_id>.partial/` exists, and the ITM library and P.1812 maps when the
   run lists them. Any refusal is a ConfigError, so the command exits 2.
2. Create `<output_dir>/<run_id>.partial/` and write the artifacts: the
   terrain build, `predictions.parquet`, the coverage files derived from it,
   the calibration files when the config names observations, and
   `run_report.md`.
3. Write `manifest.json`, then rename the folder to `<run_id>/`.

A failure in step 2 or 3 is logged as `engine_run_failed` with the run id and
the `.partial` path, then propagates: the folder stays `.partial` without a
manifest, and nothing deletes it. The engine never deletes a run folder.
"""

import re
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import rasterio
import structlog
from rasterio.io import DatasetReader

from engine.buildings import SHARE_RADIUS_M, Footprints
from engine.calibration import read_observations, write_calibration
from engine.config import RunConfig, load_config
from engine.coverage import write_coverage
from engine.errors import ConfigError
from engine.grid import RunGrid, run_grid
from engine.manifest import write_run_manifest
from engine.points import Endpoint, box_form, read_points
from engine.predict import PairPrediction, RunModels, predict_pairs
from engine.predictions import P1812_QUANTITIES, write_predictions
from engine.prepared import require_admissible_layer
from engine.provenance import Provenance, collect_provenance
from engine.report import report_markdown
from engine.settings import EngineSettings
from engine.snapshots import BUILDINGS_FILE, NamedSnapshot, resolve_snapshots
from engine.terrain_build import (
    FINE_RESOLUTION_M,
    TerrainBuild,
    build_terrain,
    check_sources,
    dem_resolution_m,
)
from terrain.cells import FineLayers
from terrain.draws import FineProfileBuilder, MetroProfileBuilder, ProfileBuilder
from terrain.itm import load_itm_library
from terrain.path_loss import DrawModels

log = structlog.get_logger(component="engine.run")

PARTIAL_SUFFIX = ".partial"
SITE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


@dataclass(frozen=True, kw_only=True)
class RunInputs:
    """Everything a run reads, checked before anything is written."""

    config: RunConfig
    snapshots: dict[str, NamedSnapshot]
    provenance: Provenance
    sites: list[Endpoint]
    receivers: list[Endpoint]
    observations: pd.DataFrame | None
    grid: RunGrid
    models: DrawModels


def run_engine(config_path: Path, settings: EngineSettings) -> Path:
    """Run the engine on one config file.

    Args:
        config_path: The YAML config.
        settings: Where snapshots and libraries are.

    Returns:
        The finished run folder, `<output_dir>/<run_id>/`.

    Raises:
        ConfigError: when anything is refused before the run starts; nothing
            is written.
    """
    started = time.monotonic()
    inputs = prepare_run(config_path, settings)
    run_id = inputs.config.run_id
    log.info("engine_run_begin", run_id=run_id, config_sha256=inputs.config.config_sha256)
    partial = start_run(inputs.config)
    try:
        artifacts = write_artifacts(inputs, partial)
        with _step("manifest_write", run_id):
            write_run_manifest(
                partial,
                inputs.config,
                inputs.snapshots,
                inputs.provenance,
                artifacts,
                box_forms={
                    "tx_sites": box_form(inputs.sites),
                    "rx_points": box_form(inputs.receivers),
                },
            )
        final = finish_run(partial)
    except Exception:
        # Not swallowed: logged with where the unfinished run is, then re-raised.
        log.error("engine_run_failed", run_id=run_id, partial=str(partial))
        raise
    log.info(
        "engine_run_end",
        run_id=run_id,
        path=str(final),
        artifacts=len(artifacts),
        duration_s=round(time.monotonic() - started, 3),
    )
    return final


def prepare_run(config_path: Path, settings: EngineSettings) -> RunInputs:
    """Check the config and everything it names, and load the models; write nothing.

    Raises:
        ConfigError: for any refusal; see the module docstring.
    """
    config = load_config(config_path)
    snapshots = resolve_snapshots(config.snapshots, settings.snapshots_dir)
    sites = read_points(config.tx_sites, "site_id")
    require_admissible_layer(config.tx_sites)
    _check_site_ids(sites)
    receivers = read_points(config.rx_points, "rx_id")
    require_admissible_layer(config.rx_points)
    observations = None if config.calibration is None else read_observations(config.calibration)
    grid = _grid(sites + receivers, snapshots, config.position_floor_m)
    check_sources(snapshots, grid)
    provenance = collect_provenance(config.models, settings, config.output_dir)
    _check_output(config)
    return RunInputs(
        config=config,
        snapshots=snapshots,
        provenance=provenance,
        sites=sites,
        receivers=receivers,
        observations=observations,
        grid=grid,
        models=load_models(config, snapshots, settings),
    )


def _grid(
    endpoints: list[Endpoint], snapshots: dict[str, NamedSnapshot], floor_m: float
) -> RunGrid:
    try:
        return run_grid(endpoints, dem_resolution_m(snapshots["dem"]), floor_m=floor_m)
    except ValueError as err:
        raise ConfigError(f"snapshot {snapshots['dem'].snapshot_id}: {err}") from err


def _check_site_ids(sites: list[Endpoint]) -> None:
    for site in sites:
        if not SITE_ID.fullmatch(site.endpoint_id):
            raise ConfigError(
                f"site_id {site.endpoint_id!r} names a coverage file; use letters, digits, "
                "'.', '_' and '-'"
            )


def _check_output(config: RunConfig) -> None:
    for name in (config.run_id, f"{config.run_id}{PARTIAL_SUFFIX}"):
        if (config.output_dir / name).exists():
            raise ConfigError(
                f"{config.output_dir / name} exists; a run never overwrites one, and nothing "
                "deletes a run folder automatically"
            )


def load_models(
    config: RunConfig, snapshots: dict[str, NamedSnapshot], settings: EngineSettings
) -> DrawModels:
    """The run's models with the ITM library and the P.1812 maps loaded.

    Raises:
        ConfigError: when the library does not load or the maps fail their
            checks, before anything is written.
    """
    try:
        itm_library = (
            load_itm_library(settings.itm_library_path) if "itm" in config.models else None
        )
        p1812_maps = None
        if "p1812" in config.models:
            # Imported only when the run lists p1812: importing it runs Py1812's
            # map load against the in-memory placeholder (ADR 0006 decision 2).
            from terrain.p1812 import load_maps

            p1812_maps = load_maps(snapshots["p1812_maps"].folder)
    except RuntimeError as err:
        raise ConfigError(str(err)) from err
    return DrawModels(
        models=config.models,
        frequency_mhz=config.frequency_mhz,
        tx=config.tx_profile,
        rx=config.rx_profile,
        itm_library=itm_library,
        p1812_maps=p1812_maps,
    )


def start_run(config: RunConfig) -> Path:
    """Create the run's `.partial` folder."""
    config.output_dir.mkdir(parents=True, exist_ok=True)
    partial = config.output_dir / f"{config.run_id}{PARTIAL_SUFFIX}"
    partial.mkdir()
    return partial


def finish_run(partial: Path) -> Path:
    """Rename `<run_id>.partial/` to `<run_id>/` once its manifest exists."""
    final = partial.with_name(partial.name.removesuffix(PARTIAL_SUFFIX))
    if not (partial / "manifest.json").is_file():
        raise RuntimeError(f"{partial} has no manifest; it stays partial")
    return partial.rename(final)


@contextmanager
def _step(name: str, run_id: str) -> Iterator[None]:
    """Log a write step's begin and end with its duration."""
    started = time.monotonic()
    log.info(f"{name}_begin", run_id=run_id)
    yield
    log.info(f"{name}_end", run_id=run_id, duration_s=round(time.monotonic() - started, 3))


def write_artifacts(inputs: RunInputs, partial: Path) -> list[Path]:
    """Write every artifact into the `.partial` folder; returns them in the order written."""
    config, run_id = inputs.config, inputs.config.run_id
    with _step("terrain_build", run_id):
        terrain = build_terrain(inputs.snapshots, inputs.grid, partial / "terrain")
    pairs, absent = predict(inputs, terrain)
    predictions = partial / "predictions.parquet"
    with _step("predictions_write", run_id):
        write_predictions(predictions, pairs, config.models, absent)
    with _step("coverage_write", run_id):
        coverage = write_coverage(partial, config.models, inputs.grid)
    calibration = []
    if inputs.observations is not None:
        with _step("calibration_write", run_id):
            calibration = write_calibration(partial, inputs.observations, config.models, run_id)
    report = partial / "run_report.md"
    with _step("run_report_write", run_id):
        report.write_text(report_markdown(run_id, pairs, terrain), encoding="utf-8", newline="\n")
    layers = [terrain.dem, terrain.dsm, terrain.canopy, terrain.clutter]
    return [*(p for p in layers if p is not None), predictions, *coverage, *calibration, report]


def predict(
    inputs: RunInputs, terrain: TerrainBuild
) -> tuple[list[PairPrediction], frozenset[str]]:
    """Open the terrain build and predict every pair.

    Returns:
        Every pair's prediction, and the quantities the run's draws cannot
        report: P.1812's without its 10 m profile or without p1812.
    """
    with ExitStack() as stack:
        dem = stack.enter_context(rasterio.open(terrain.dem))
        builder = _builder(stack, dem, terrain)
        footprints = _footprints(inputs.snapshots, inputs.grid)
        pixels = None
        if footprints is not None and terrain.resolution_m == FINE_RESOLUTION_M:
            pixels = footprints.pixels(dem)
        run = RunModels(
            builder=builder,
            models=inputs.models,
            run_seed=inputs.config.seed,
            endpoint_draws=inputs.config.endpoint_draws,
            position_floor_m=inputs.config.position_floor_m,
            footprints=footprints,
            building_pixels=pixels,
        )
        cells = isinstance(builder, FineProfileBuilder) and "p1812" in inputs.config.models
        absent = frozenset() if cells else P1812_QUANTITIES
        return predict_pairs(inputs.sites, inputs.receivers, run), absent


def _builder(stack: ExitStack, dem: DatasetReader, terrain: TerrainBuild) -> ProfileBuilder:
    """1 m ground and P.1812 cells with a DSM or canopy; ground alone at the DEM step otherwise."""
    if terrain.dsm is None and terrain.canopy is None:
        return MetroProfileBuilder(dem=dem, step_m=terrain.resolution_m)
    dsm, canopy = (
        None if path is None else stack.enter_context(rasterio.open(path))
        for path in (terrain.dsm, terrain.canopy)
    )
    return FineProfileBuilder(layers=FineLayers(dem=dem, dsm=dsm, canopy=canopy))


def _footprints(snapshots: dict[str, NamedSnapshot], grid: RunGrid) -> Footprints | None:
    """The buildings snapshot's footprints that reach within 100 m of the run grid."""
    buildings = snapshots.get("buildings")
    if buildings is None:
        return None
    reach_m = SHARE_RADIUS_M
    window_m = (
        grid.west_m - reach_m,
        grid.south_m - reach_m,
        grid.east_m + reach_m,
        grid.north_m + reach_m,
    )
    return Footprints.from_parquet(buildings.folder / BUILDINGS_FILE, window_m=window_m)
