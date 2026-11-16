"""One engine run: check everything, write into `<run_id>.partial/`, manifest last, rename.

The order is the contract of `docs/INTERFACE.md`:

1. Check, writing nothing: the config and profiles, every named snapshot
   against its manifest, the point layers, the observations, the run grid
   against the DEM, the code provenance, and that neither `<run_id>/` nor
   `<run_id>.partial/` exists. Any refusal is a ConfigError.
2. Create `<output_dir>/<run_id>.partial/` and write the artifacts: the
   terrain build, `predictions.parquet`, the coverage files derived from it,
   the calibration files when the config names observations, and
   `run_report.md`.
3. Write `manifest.json`, then rename the folder to `<run_id>/`.

A failure in step 2 or 3 propagates: the folder stays `.partial` without a
manifest, and nothing deletes it. The engine never deletes a run folder.
"""

import re
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import rasterio
import structlog

from engine.buildings import Footprints
from engine.calibration import read_observations, write_calibration
from engine.config import RunConfig, load_config
from engine.coverage import write_coverage
from engine.errors import ConfigError
from engine.grid import RunGrid, run_grid
from engine.manifest import write_run_manifest
from engine.points import Endpoint, read_points
from engine.predict import PairPrediction, RunModels, predict_pairs
from engine.predictions import write_predictions
from engine.provenance import Provenance, collect_provenance
from engine.report import report_markdown
from engine.settings import EngineSettings
from engine.snapshots import BUILDINGS_FILE, NamedSnapshot, resolve_snapshots
from engine.terrain_build import TerrainBuild, build_terrain, check_sources, dem_resolution_m
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
    log.info(
        "engine_run_begin", run_id=inputs.config.run_id, config_sha256=inputs.config.config_sha256
    )
    partial = start_run(inputs.config)
    artifacts = write_artifacts(inputs, partial, settings)
    write_run_manifest(partial, inputs.config, inputs.snapshots, inputs.provenance, artifacts)
    final = finish_run(partial)
    log.info(
        "engine_run_end",
        run_id=inputs.config.run_id,
        path=str(final),
        artifacts=len(artifacts),
        duration_s=round(time.monotonic() - started, 3),
    )
    return final


def prepare_run(config_path: Path, settings: EngineSettings) -> RunInputs:
    """Check the config and everything it names; write nothing.

    Raises:
        ConfigError: for any refusal; see the module docstring.
    """
    config = load_config(config_path)
    snapshots = resolve_snapshots(config.snapshots, settings.snapshots_dir)
    sites = read_points(config.tx_sites, "site_id")
    _check_site_ids(sites)
    receivers = read_points(config.rx_points, "rx_id")
    observations = None if config.calibration is None else read_observations(config.calibration)
    grid = run_grid(sites + receivers, dem_resolution_m(snapshots["dem"]))
    check_sources(snapshots, grid)
    provenance = collect_provenance(config.models, settings)
    _check_output(config)
    return RunInputs(
        config=config,
        snapshots=snapshots,
        provenance=provenance,
        sites=sites,
        receivers=receivers,
        observations=observations,
        grid=grid,
    )


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


def write_artifacts(inputs: RunInputs, partial: Path, settings: EngineSettings) -> list[Path]:
    """Write every artifact into the `.partial` folder; returns them in the order written."""
    config = inputs.config
    terrain = build_terrain(inputs.snapshots, inputs.grid, partial / "terrain")
    pairs = predict(inputs, terrain, settings)
    predictions = partial / "predictions.parquet"
    write_predictions(predictions, pairs, config.models)
    coverage = write_coverage(partial, config.models, inputs.grid)
    calibration = []
    if inputs.observations is not None:
        calibration = write_calibration(partial, inputs.observations, config.models, config.run_id)
    report = partial / "run_report.md"
    report.write_text(
        report_markdown(config.run_id, pairs, terrain), encoding="utf-8", newline="\n"
    )
    layers = [terrain.dem, terrain.dsm, terrain.canopy, terrain.clutter]
    return [*(p for p in layers if p is not None), predictions, *coverage, *calibration, report]


def predict(
    inputs: RunInputs, terrain: TerrainBuild, settings: EngineSettings
) -> list[PairPrediction]:
    """Open the terrain build and predict every pair."""
    with ExitStack() as stack:
        dem = stack.enter_context(rasterio.open(terrain.dem))
        builder: ProfileBuilder
        if terrain.dsm is None and terrain.canopy is None:
            builder = MetroProfileBuilder(dem=dem, step_m=terrain.resolution_m)
        else:
            dsm = None if terrain.dsm is None else stack.enter_context(rasterio.open(terrain.dsm))
            canopy = (
                None
                if terrain.canopy is None
                else stack.enter_context(rasterio.open(terrain.canopy))
            )
            builder = FineProfileBuilder(layers=FineLayers(dem=dem, dsm=dsm, canopy=canopy))
        footprints = _footprints(inputs.snapshots)
        pixels = None
        if footprints is not None and isinstance(builder, FineProfileBuilder):
            pixels = footprints.pixels(dem)
        run = RunModels(
            builder=builder,
            models=_models(inputs, settings),
            run_seed=inputs.config.seed,
            endpoint_draws=inputs.config.endpoint_draws,
            footprints=footprints,
            building_pixels=pixels,
        )
        return predict_pairs(inputs.sites, inputs.receivers, run)


def _footprints(snapshots: dict[str, NamedSnapshot]) -> Footprints | None:
    buildings = snapshots.get("buildings")
    return None if buildings is None else Footprints.from_parquet(buildings.folder / BUILDINGS_FILE)


def _models(inputs: RunInputs, settings: EngineSettings) -> DrawModels:
    config = inputs.config
    itm_library = load_itm_library(settings.itm_library_path) if "itm" in config.models else None
    p1812_maps = None
    if "p1812" in config.models:
        # Imported only when the run lists p1812: importing it runs Py1812's
        # map load against the in-memory placeholder (ADR 0006 decision 2).
        from terrain.p1812 import load_maps

        p1812_maps = load_maps(inputs.snapshots["p1812_maps"].folder)
    return DrawModels(
        models=config.models,
        frequency_mhz=config.frequency_mhz,
        tx=config.tx_profile,
        rx=config.rx_profile,
        itm_library=itm_library,
        p1812_maps=p1812_maps,
    )
