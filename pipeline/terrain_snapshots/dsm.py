"""The DSM snapshot: a 1 m first-return surface built with PDAL from the 3DEP EPT.

The lidar points are read over each window through `make pdal` (platform-eng,
PR #34) and never kept. The snapshot holds the DSM as a COG on the 1 m DEM
tile's grid, the PDAL pipeline file of every window (each file's sha256 is in
the manifest), the EPT URL and the bounds. Recipe A1 is in dsm_recipe, the
thin-output guard in dsm_guard.

Before anything is written the command refuses when the OpenStreetMap
snapshot it reads water from is missing or differs from its manifest, when the
PDAL image reports another version than the one pinned, or when the scratch
folder lies inside the snapshots folder. Window rasters go to
`<scratch>/<snapshot id>/`, outside the snapshot; only the joined DSM, the
pipelines and the README go into `<id>.partial/`. A window that is all water
gets no pipeline and no PDAL run; its cells are nodata and it is recorded as
`water_only`.

Manifest fields beside snp_01's: the EPSG label and the true horizontal datum,
NAD83(2011), with the vertical datum (Casey, PR #34); the PDAL version the
image runs and the image digest; the recipe; the guard's density and factor;
the OSM snapshot used; per window and in total the land area, water area,
point count and nodata cells.
"""

import json
import re
import subprocess
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import numpy.typing as npt
import rasterio
import rasterio.shutil
from rasterio import windows as rio_windows
from rasterio.transform import from_origin

from pipeline.common.settings import REPO_ROOT
from pipeline.common.snapshot import (
    MANIFEST_NAME,
    SnapshotError,
    sha256_file,
    snapshot_folder,
    verified_manifest,
)
from pipeline.terrain_snapshots.common import build_snapshot, timed, today_utc, write_readme
from pipeline.terrain_snapshots.dem import HORIZONTAL_DATUM, LICENCE, VERTICAL_DATUM
from pipeline.terrain_snapshots.dsm_guard import (
    BASIS,
    NOMINAL_FIRST_RETURNS_PER_M2,
    THIN_FACTOR,
    ThinOutputError,
    WindowCount,
    check_build,
    check_window,
    land_points,
    water_mask,
)
from pipeline.terrain_snapshots.dsm_recipe import (
    CRS,
    EPT_URL,
    HALO_CELLS,
    NODATA,
    WHERE,
    Grid,
    Window,
    pipeline_stages,
    split,
)

PDAL_VERSION = "2.8.4 (git-version: 156082)"
PDAL_IMAGE = (
    "pdal/pdal:2.9.2@sha256:18d73101500cd673052ee4ead8a4b716a9dad09c8711e88e7a37abed2496c469"
)
RECIPE = {
    "name": "A1 (Casey, PR #34, unit 5b)",
    "returns": "first returns only, ReturnNumber 1, single returns included",
    "dropped": "classes 7 (low noise) and 18 (high noise), withheld points",
    "statistic": "highest point per 1 m cell, writers.gdal binmode, on the DEM tile's grid",
    "gap_fill": "one-cell gaps filled from neighbouring cells, writers.gdal window_size 1",
    "where": WHERE,
}
Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


class OsmSnapshotMissingError(SnapshotError):
    """The OSM snapshot the DSM reads water from is missing, unfinished or changed."""


@dataclass(frozen=True)
class DsmSource:
    """The EPT, the grid to build on, and how many windows a side to build it in."""

    snapshot_id: str
    ept_url: str
    grid: Grid
    windows_per_side: int


X72Y429_DSM = DsmSource(
    snapshot_id="ept-mo-stlouis-2017-x72y429",
    ept_url=EPT_URL,
    grid=Grid(west=719_994.0, south=4_279_994.0, width=10_012, height=10_012),
    windows_per_side=4,
)


def make_runner(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run one make target from the checkout, capturing its output."""
    return subprocess.run(
        ["make", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )


def osm_water(root: Path, osm_snapshot_id: str) -> tuple[gpd.GeoDataFrame, dict[str, str]]:
    """The water polygons of a finished, verified OSM snapshot, in EPSG:26915.

    Raises:
        OsmSnapshotMissingError: naming the command that writes the snapshot.
    """
    try:
        folder = snapshot_folder(root, osm_snapshot_id)
        verified_manifest(folder)
    except SnapshotError as err:
        raise OsmSnapshotMissingError(
            f"the DSM needs the OSM snapshot {osm_snapshot_id} under {root}, finished and "
            f"matching its manifest ({err}); write it first with "
            "`python -m pipeline.terrain_snapshots osm`"
        ) from err
    water = gpd.read_parquet(folder / "water.parquet").to_crs(CRS)
    return water, {"id": osm_snapshot_id, "manifest_sha256": sha256_file(folder / MANIFEST_NAME)}


def check_pdal_version(runner: Runner) -> str:
    """The version `make check-pdal` reports, refused unless it is PDAL_VERSION.

    Raises:
        SnapshotError: naming the version found.
    """
    done = runner(["check-pdal"])
    found = re.search(r"pdal (\d+\.\d+\.\d+ \(git-version: \w+\))", done.stdout)
    reported = found.group(1) if found else done.stdout.strip()[:200]
    if done.returncode != 0 or reported != PDAL_VERSION:
        raise SnapshotError(f"make check-pdal reports {reported!r}, not {PDAL_VERSION!r}")
    return reported


def scratch_folder(root: Path, scratch: Path, snapshot_id: str) -> Path:
    """Create `<scratch>/<snapshot id>/` for the window rasters.

    Raises:
        SnapshotError: when scratch is or lies inside the snapshots folder, or
            the run folder exists from an earlier run.
    """
    snapshots, work = root.resolve(), scratch.resolve()
    if work == snapshots or snapshots in work.parents or work in snapshots.parents:
        raise SnapshotError(f"scratch {scratch} overlaps the snapshots folder {root}")
    run = scratch / snapshot_id
    if run.exists():
        raise SnapshotError(f"scratch {run} exists from an earlier run; Casey decides what to do")
    run.mkdir(parents=True)
    return run


def _write_pipeline(partial: Path, source: DsmSource, window: Window) -> Path:
    path = partial / "pipelines" / f"{window.name}.json"
    path.parent.mkdir(exist_ok=True)
    stages = pipeline_stages(source.grid, window, source.ept_url)
    path.write_text(json.dumps(stages, indent=2) + "\n", encoding="utf-8")
    return path


def _run_pdal(runner: Runner, pipeline: Path, workdir: Path) -> None:
    done = runner(["pdal", f"PIPELINE={pipeline.as_posix()}", f"WORKDIR={workdir.as_posix()}"])
    if done.returncode != 0:
        raise SnapshotError(
            f"make pdal failed for {pipeline.name} (exit {done.returncode}): "
            f"{done.stderr.strip()[-500:]}"
        )


FloatGrid = npt.NDArray[np.float32]


def read_window_output(path: Path, window: Window) -> tuple[FloatGrid, FloatGrid]:
    """The max and count bands of one window's writers.gdal output, halo removed.

    Raises:
        ThinOutputError: when PDAL wrote no file.
        SnapshotError: when the file lacks a band or has another size.
    """
    if not path.is_file():
        raise ThinOutputError(f"PDAL wrote no {path.name}")
    halo = 2 * HALO_CELLS
    with rasterio.open(path) as src:
        if (src.width, src.height) != (window.width + halo, window.height + halo):
            raise SnapshotError(f"{path.name} is {src.width} by {src.height} cells")
        bands = list(src.descriptions)
        if "max" not in bands or "count" not in bands:
            raise SnapshotError(f"{path.name} has bands {bands}, not max and count")
        inner = rio_windows.Window(HALO_CELLS, HALO_CELLS, window.width, window.height)
        heights = src.read(bands.index("max") + 1, window=inner).astype(np.float32)
        counts = src.read(bands.index("count") + 1, window=inner).astype(np.float32)
    return heights, counts


@dataclass(frozen=True)
class _Context:
    source: DsmSource
    partial: Path
    workdir: Path
    water: gpd.GeoDataFrame
    runner: Runner


def build_window(ctx: _Context, window: Window) -> tuple[dict[str, Any], FloatGrid]:
    """Build and guard one window.

    Returns:
        The window's manifest record and its DSM cells (all nodata when the
        window is water only).

    Raises:
        ThinOutputError: from the guard.
        SnapshotError: when make pdal fails or its output is malformed.
    """
    grid = ctx.source.grid
    west, south, east, north = window.bounds(grid)
    transform = from_origin(west, north, grid.resolution, grid.resolution)
    wet = water_mask(ctx.water, transform, (window.height, window.width))
    cell_m2 = grid.resolution * grid.resolution
    land_m2, water_m2 = float((~wet).sum()) * cell_m2, float(wet.sum()) * cell_m2
    record: dict[str, Any] = {"name": window.name, "bounds": [west, south, east, north]}
    if land_m2 == 0.0:
        counted = check_window(window.name, land_m2, water_m2, 0)
        heights = np.full((window.height, window.width), NODATA, dtype=np.float32)
        record |= {"pipeline": None, "pipeline_sha256": None}
    else:
        pipeline = _write_pipeline(ctx.partial, ctx.source, window)
        _run_pdal(ctx.runner, pipeline, ctx.workdir)
        heights, counts = read_window_output(ctx.workdir / f"{window.name}.tif", window)
        counted = check_window(window.name, land_m2, water_m2, land_points(counts, wet, NODATA))
        relative = pipeline.relative_to(ctx.partial).as_posix()
        record |= {"pipeline": relative, "pipeline_sha256": sha256_file(pipeline)}
    record |= {k: v for k, v in asdict(counted).items() if k != "name"}
    record["nodata_cells"] = int((heights == NODATA).sum())
    return record, heights


@contextmanager
def _mosaic(path: Path, grid: Grid) -> Iterator[Any]:
    """A tiled float32 GeoTIFF on the grid, open for windowed writes."""
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=grid.width,
        height=grid.height,
        count=1,
        dtype="float32",
        crs=CRS,
        nodata=NODATA,
        tiled=True,
        blockxsize=256,
        blockysize=256,
        transform=from_origin(grid.west, grid.north, grid.resolution, grid.resolution),
        compress="DEFLATE",
    ) as dst:
        yield dst


def build_windows(ctx: _Context) -> list[dict[str, Any]]:
    """Build every window into a scratch mosaic, then copy it as the snapshot's COG."""
    mosaic = ctx.workdir / "dsm_full.tif"
    records = []
    with _mosaic(mosaic, ctx.source.grid) as dst:
        for window in split(ctx.source.grid, ctx.source.windows_per_side):
            with timed("dsm_window", window=window.name) as details:
                record, heights = build_window(ctx, window)
                details.update(
                    status=record["status"],
                    points=record["points"],
                    land_area_m2=record["land_area_m2"],
                )
            target = rio_windows.Window(window.col_off, window.row_off, window.width, window.height)
            dst.write(heights, 1, window=target)
            records.append(record)
    rasterio.shutil.copy(
        mosaic,
        ctx.partial / "dsm.tif",
        driver="COG",
        COMPRESS="DEFLATE",
        PREDICTOR="YES",
        BLOCKSIZE=512,
    )
    return records


def build_dsm(
    root: Path,
    osm_snapshot_id: str,
    scratch: Path,
    *,
    source: DsmSource = X72Y429_DSM,
    runner: Runner = make_runner,
    today: date | None = None,
) -> Path:
    """Build the first-return DSM over a grid with PDAL and write it as a finished snapshot.

    Args:
        root: The snapshots folder.
        osm_snapshot_id: The finished OSM snapshot whose water polygons the guard reads.
        scratch: A folder outside root for the window rasters.
        source: The EPT, grid and windows; x72y429 by default.
        runner: Runs a make target; make_runner in production.
        today: The build date, the snapshot's capture_date; the UTC date by default.

    Raises:
        OsmSnapshotMissingError: before anything runs.
        ThinOutputError: when a window or the build holds too few points over land.
        SnapshotError: for another PDAL version, an existing snapshot or a
            failed PDAL run.
    """
    with timed("dsm_snapshot", snapshot_id=source.snapshot_id) as details:
        water, osm = osm_water(root, osm_snapshot_id)
        version = check_pdal_version(runner)
        workdir = scratch_folder(root, scratch, source.snapshot_id)

        def fill(partial: Path) -> dict[str, object]:
            records = build_windows(_Context(source, partial, workdir, water, runner))
            totals = check_build([_count(record) for record in records])
            totals["nodata_cells"] = sum(record["nodata_cells"] for record in records)
            details.update(totals)
            _write_dsm_readme(partial, source, osm["id"])
            return _manifest_fields(source, version, osm, records, totals)

        metadata = {
            "capture_date": (today or today_utc()).isoformat(),
            "source_url": source.ept_url,
            "licence": LICENCE,
            "seeds": {},
        }
        folder = build_snapshot(root, source.snapshot_id, fill, metadata)
        details["path"] = str(folder)
    return folder


def _count(record: dict[str, Any]) -> WindowCount:
    return WindowCount(
        record["name"],
        record["land_area_m2"],
        record["water_area_m2"],
        record["points"],
        record["status"],
    )


def _manifest_fields(
    source: DsmSource,
    version: str,
    osm: dict[str, str],
    records: list[dict[str, Any]],
    totals: dict[str, float | int],
) -> dict[str, object]:
    grid = source.grid
    return {
        "ept_url": source.ept_url,
        "bounds_epsg26915": [grid.west, grid.south, grid.east, grid.north],
        "grid": asdict(grid),
        "crs": CRS,
        "horizontal_datum": HORIZONTAL_DATUM,
        "horizontal_datum_note": "the lidar is NAD83(2011) / UTM 15N (EPSG:6344); with PROJ's "
        "network off EPSG:26915 gives the same numbers (PR #34)",
        "vertical_datum": VERTICAL_DATUM,
        "pdal_version": version,
        "pdal_image": PDAL_IMAGE,
        "proj_network": "OFF",
        "recipe": RECIPE,
        "guard": {
            "nominal_first_returns_per_m2": NOMINAL_FIRST_RETURNS_PER_M2,
            "factor": THIN_FACTOR,
            "basis": BASIS,
            "land": "window cells whose centre is outside every OSM water polygon",
        },
        "osm_snapshot": osm,
        "windows": records,
        "totals": totals,
    }


def _write_dsm_readme(partial: Path, source: DsmSource, osm_id: str) -> None:
    write_readme(
        partial,
        source.snapshot_id,
        f"""
First-return digital surface model (roof and tree tops, leaf-off lidar) at 1 m on the DEM
tile's grid, EPSG:26915, built with PDAL {PDAL_VERSION} from {source.ept_url} over the bounds in
the manifest. Recipe A1: first returns, classes 7 and 18 and withheld points dropped, highest
point per cell, one-cell gaps filled. No point cloud is kept. `pipelines/` holds each window's
PDAL pipeline. Water from {osm_id} sets each window's land area for the thin-output guard;
all-water windows are nodata. Public domain (USGS 3DEP).

Written by `python -m pipeline.terrain_snapshots dsm`. Never edited after the manifest.
""",
    )
