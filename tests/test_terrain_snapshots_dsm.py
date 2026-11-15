"""The DSM command: pipelines, the faux PDAL run, the mosaic, the manifest and the refusals.

A fake runner stands in for `make check-pdal` and `make pdal`: it reads the
pipeline the command wrote and writes the two-band raster (max, count) that
writers.gdal would, on the grid the pipeline names. Nothing touches Docker
or the network.
"""

import json
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box
from structlog.testing import capture_logs

from pipeline.common.settings import REPO_ROOT
from pipeline.common.snapshot import (
    SnapshotError,
    SnapshotExistsError,
    finish_snapshot,
    read_manifest,
    sha256_file,
    start_snapshot,
    verify_manifest,
    write_manifest,
)
from pipeline.terrain_snapshots.dem import X72Y429 as DEM_X72Y429
from pipeline.terrain_snapshots.dsm import (
    PDAL_IMAGE,
    PDAL_VERSION,
    X72Y429_DSM,
    DsmSource,
    OsmSnapshotMissingError,
    build_dsm,
    check_pdal_version,
    scratch_folder,
)
from pipeline.terrain_snapshots.dsm_guard import ThinOutputError
from pipeline.terrain_snapshots.dsm_recipe import (
    EPT_URL,
    WHERE,
    Grid,
    pipeline_stages,
    split,
)

OSM_ID = "osm-geofabrik-mo-il-2026-11-01"
TODAY = date(2026, 11, 15)
GRID = Grid(west_m=720_000.0, south_m=4_280_000.0, width=8, height=8)
SOURCE = DsmSource(snapshot_id="ept-test-dsm", ept_url=EPT_URL, grid=GRID, windows_per_side=2)
VERSION_OUTPUT = f"pdal {PDAL_VERSION}\nreaders.ept and writers.gdal present\n"


@dataclass
class FakeMake:
    """Answers `make check-pdal` and `make pdal` the way the real targets would."""

    count: Callable[[int, int], float] = lambda row, col: 4.0
    version_output: str = VERSION_OUTPUT
    returncode: int = 0
    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        if args[0] == "check-pdal":
            return subprocess.CompletedProcess(list(args), 0, self.version_output, "")
        options = dict(arg.split("=", 1) for arg in args[1:])
        if self.returncode == 0:
            self._write(Path(options["PIPELINE"]), Path(options["WORKDIR"]))
        return subprocess.CompletedProcess(list(args), self.returncode, "", "pdal failed")

    def _write(self, pipeline: Path, workdir: Path) -> None:
        writer = json.loads(pipeline.read_text())[-1]
        width, height = writer["width"], writer["height"]
        rows, cols = np.indices((height, width))
        top = writer["origin_y"] + height * writer["resolution"]
        # 200 m at the north-west cell of GRID, +1 m a column east, +10 m a row south,
        # so a one-cell shift in either direction changes every value.
        east_m = writer["origin_x"] + cols + 0.5 - 720_000.5
        south_m = 4_280_007.5 - (top - rows - 0.5)
        heights = (200.0 + east_m + 10.0 * south_m).astype(np.float32)
        counts = np.vectorize(self.count)(rows, cols).astype(np.float32)
        with rasterio.open(
            workdir / writer["filename"].removeprefix("/work/"),
            "w",
            driver="GTiff",
            width=width,
            height=height,
            count=2,
            dtype="float32",
            crs="EPSG:26915",
            transform=from_origin(writer["origin_x"], top, 1.0, 1.0),
            nodata=-9999.0,
        ) as dst:
            dst.write(np.stack([heights, counts]))
            dst.set_band_description(1, "max")
            dst.set_band_description(2, "count")


def _osm_snapshot(root: Path, water_26915: list[tuple[float, float, float, float]]) -> Path:
    partial = start_snapshot(root, OSM_ID)
    water = gpd.GeoDataFrame(
        {"id": list(range(len(water_26915))), "osm_type": "way", "kind": "natural=water"},
        geometry=[box(*bounds) for bounds in water_26915],
        crs="EPSG:26915",
    ).to_crs("EPSG:4326")
    water.to_parquet(partial / "water.parquet", index=False)
    metadata = {
        "snapshot_id": OSM_ID,
        "capture_date": "2026-11-01",
        "source_url": "x",
        "licence": "ODbL",
        "seeds": {},
    }
    write_manifest(partial, metadata)
    return finish_snapshot(partial)


def _registered(root: Path) -> Callable[[str], str | None]:
    """Stands in for the raw.snapshot lookup: every finished folder under root is registered."""

    def lookup(snapshot_id: str) -> str | None:
        manifest = root / snapshot_id / "manifest.json"
        return sha256_file(manifest) if manifest.is_file() else None

    return lookup


def _dirs(tmp_path: Path) -> tuple[Path, Path]:
    root, scratch = tmp_path / "snapshots", tmp_path / "scratch"
    root.mkdir()
    scratch.mkdir()
    return root, scratch


# The north-east window, columns 4 to 7 and rows 0 to 3, is all water.
NE_WATER = (720_003.75, 4_280_003.75, 720_008.25, 4_280_008.25)


def test_builds_the_dsm_and_records_every_window(tmp_path: Path) -> None:
    root, scratch = _dirs(tmp_path)
    osm = _osm_snapshot(root, [NE_WATER])
    make = FakeMake()
    folder = build_dsm(
        root, OSM_ID, scratch, source=SOURCE, runner=make, today=TODAY, registered=_registered(root)
    )
    assert verify_manifest(folder) == []
    with rasterio.open(folder / "dsm.tif") as dsm:
        assert dsm.crs.to_epsg() == 26915
        assert (dsm.width, dsm.height, dsm.nodata) == (8, 8, -9999.0)
        assert tuple(dsm.bounds) == (720_000.0, 4_280_000.0, 720_008.0, 4_280_008.0)
        values = dsm.read(1)
    assert (values[:4, 4:] == -9999.0).all()
    assert values[7, 0] == 270.0 and values[7, 3] == 273.0
    assert values[2, 1] == 221.0 and values[5, 2] == 252.0
    manifest = read_manifest(folder)
    assert manifest["crs"] == "EPSG:26915"
    assert manifest["horizontal_datum"] == "NAD83(2011)"
    assert manifest["pdal_version"] == PDAL_VERSION == "2.8.4 (git-version: 156082)"
    assert manifest["pdal_image"] == PDAL_IMAGE
    assert manifest["osm_snapshot"] == {
        "id": OSM_ID,
        "manifest_sha256": sha256_file(osm / "manifest.json"),
    }
    windows = {window["name"]: window for window in manifest["windows"]}
    assert windows["r0c1"]["status"] == "water_only"
    assert windows["r0c1"]["land_area_m2"] == 0.0 and windows["r0c1"]["water_area_m2"] == 16.0
    assert windows["r0c0"] | {"pipeline_sha256": None} == {
        "name": "r0c0",
        "bounds": [720_000.0, 4_280_004.0, 720_004.0, 4_280_008.0],
        "land_area_m2": 16.0,
        "water_area_m2": 0.0,
        "points": 64,
        "status": "built",
        "pipeline": "pipelines/r0c0.json",
        "pipeline_sha256": None,
        "nodata_cells": 0,
    }
    assert windows["r0c0"]["pipeline_sha256"] == sha256_file(folder / "pipelines" / "r0c0.json")
    assert manifest["totals"] == {
        "land_area_m2": 48.0,
        "water_area_m2": 16.0,
        "points": 192,
        "nodata_cells": 16,
    }
    assert sorted(path.name for path in (folder / "pipelines").iterdir()) == [
        "r0c0.json",
        "r1c0.json",
        "r1c1.json",
    ]
    assert [call[0] for call in make.calls] == ["check-pdal", "pdal", "pdal", "pdal"]


def test_refuses_without_the_osm_snapshot_before_anything_runs(tmp_path: Path) -> None:
    root, scratch = _dirs(tmp_path)
    make = FakeMake()
    with pytest.raises(OsmSnapshotMissingError, match="osm"):
        build_dsm(
            root,
            OSM_ID,
            scratch,
            source=SOURCE,
            runner=make,
            today=TODAY,
            registered=_registered(root),
        )
    assert make.calls == []
    assert list(root.iterdir()) == []


def test_refuses_an_osm_snapshot_whose_files_changed(tmp_path: Path) -> None:
    root, scratch = _dirs(tmp_path)
    osm = _osm_snapshot(root, [])
    (osm / "water.parquet").write_bytes(b"changed")
    with pytest.raises(OsmSnapshotMissingError, match="differ"):
        build_dsm(
            root,
            OSM_ID,
            scratch,
            source=SOURCE,
            runner=FakeMake(),
            today=TODAY,
            registered=_registered(root),
        )


def test_a_thin_run_fails_and_leaves_no_finished_snapshot(tmp_path: Path) -> None:
    root, scratch = _dirs(tmp_path)
    _osm_snapshot(root, [])
    make = FakeMake(count=lambda row, col: 1.0 if row == col else 0.0)
    with pytest.raises(ThinOutputError):
        build_dsm(
            root,
            OSM_ID,
            scratch,
            source=SOURCE,
            runner=make,
            today=TODAY,
            registered=_registered(root),
        )
    assert not (root / SOURCE.snapshot_id).exists()


def test_refuses_another_pdal_version_before_starting(tmp_path: Path) -> None:
    root, scratch = _dirs(tmp_path)
    _osm_snapshot(root, [])
    make = FakeMake(version_output="pdal 2.9.2 (git-version: abc)\n")
    with pytest.raises(SnapshotError, match="2.9.2"):
        build_dsm(
            root,
            OSM_ID,
            scratch,
            source=SOURCE,
            runner=make,
            today=TODAY,
            registered=_registered(root),
        )
    assert not (root / f"{SOURCE.snapshot_id}.partial").exists()


def test_a_failed_pdal_run_stops_the_build(tmp_path: Path) -> None:
    root, scratch = _dirs(tmp_path)
    _osm_snapshot(root, [])
    with pytest.raises(SnapshotError, match="pdal failed"):
        build_dsm(
            root,
            OSM_ID,
            scratch,
            source=SOURCE,
            runner=FakeMake(returncode=1),
            today=TODAY,
            registered=_registered(root),
        )


def test_refuses_a_scratch_folder_inside_the_snapshots_folder(tmp_path: Path) -> None:
    root, _ = _dirs(tmp_path)
    _osm_snapshot(root, [])
    with pytest.raises(SnapshotError, match="scratch"):
        build_dsm(
            root,
            OSM_ID,
            root / "work",
            source=SOURCE,
            runner=FakeMake(),
            today=TODAY,
            registered=_registered(root),
        )


def test_the_pipeline_is_recipe_a1_on_a_grid_one_cell_wider_than_the_window() -> None:
    window = split(GRID, 2)[0]
    reader, reprojection, writer = pipeline_stages(GRID, window, EPT_URL)
    assert reader == {
        "type": "readers.ept",
        "filename": EPT_URL,
        "bounds": "([719999.0, 720005.0], [4280003.0, 4280009.0])/EPSG:26915",
    }
    assert reprojection == {"type": "filters.reprojection", "out_srs": "EPSG:26915"}
    assert WHERE == (
        "ReturnNumber == 1 && Classification != 7 && Classification != 18 && Withheld == 0"
    )
    assert writer == {
        "type": "writers.gdal",
        "filename": "/work/r0c0.tif",
        "where": WHERE,
        "binmode": True,
        "output_type": "max,count",
        "resolution": 1.0,
        "origin_x": 719_999.0,
        "origin_y": 4_280_003.0,
        "width": 6,
        "height": 6,
        "window_size": 1,
        "data_type": "float32",
        "nodata": -9999.0,
        "override_srs": "EPSG:26915",
        "gdalopts": "COMPRESS=DEFLATE,TILED=YES",
    }


def test_x72y429_splits_into_sixteen_windows_on_the_dem_grid() -> None:
    assert X72Y429_DSM.snapshot_id == "ept-mo-stlouis-2017-x72y429"
    grid = X72Y429_DSM.grid
    assert (grid.west_m, grid.south_m, grid.width, grid.height) == (
        719_994.0,
        4_279_994.0,
        10_012,
        10_012,
    )
    assert (grid.west_m, grid.south_m, grid.east_m, grid.north_m) == DEM_X72Y429.bounds
    assert grid.resolution_m == DEM_X72Y429.resolution_m
    windows = split(grid, X72Y429_DSM.windows_per_side)
    assert len(windows) == 16
    assert {(w.width, w.height) for w in windows} == {(2503, 2503)}
    assert sum(w.width * w.height for w in windows) == 10_012 * 10_012
    assert re.fullmatch(r"r\dc\d", windows[5].name)


def test_an_existing_dsm_snapshot_is_refused_before_pdal_or_scratch(tmp_path: Path) -> None:
    root, scratch = _dirs(tmp_path)
    _osm_snapshot(root, [])
    (root / SOURCE.snapshot_id).mkdir()
    make = FakeMake()
    with pytest.raises(SnapshotExistsError):
        build_dsm(
            root,
            OSM_ID,
            scratch,
            source=SOURCE,
            runner=make,
            today=TODAY,
            registered=_registered(root),
        )
    assert make.calls == []
    assert list(scratch.iterdir()) == []


def test_refuses_an_osm_snapshot_that_is_not_registered(tmp_path: Path) -> None:
    root, scratch = _dirs(tmp_path)
    _osm_snapshot(root, [])
    make = FakeMake()
    with pytest.raises(OsmSnapshotMissingError, match="not registered"):
        build_dsm(
            root,
            OSM_ID,
            scratch,
            source=SOURCE,
            runner=make,
            today=TODAY,
            registered=lambda snapshot_id: None,
        )
    assert make.calls == []


# The west half of window r1c0 (columns 0 and 1, rows 4 to 7) is water.
HALF_WATER = (719_999.75, 4_279_999.75, 720_002.25, 4_280_003.75)


def test_a_part_water_window_counts_land_only_and_keeps_its_surface(tmp_path: Path) -> None:
    root, scratch = _dirs(tmp_path)
    _osm_snapshot(root, [HALF_WATER])
    # The fake writes 4 points in every cell, water cells included.
    folder = build_dsm(
        root,
        OSM_ID,
        scratch,
        source=SOURCE,
        runner=FakeMake(),
        today=TODAY,
        registered=_registered(root),
    )
    window = {w["name"]: w for w in read_manifest(folder)["windows"]}["r1c0"]
    assert window["status"] == "built"
    assert (window["land_area_m2"], window["water_area_m2"]) == (8.0, 8.0)
    assert window["points"] == 32
    with rasterio.open(folder / "dsm.tif") as dsm:
        assert dsm.read(1)[4, 0] == 240.0


def test_the_pdal_image_is_the_one_compose_pins() -> None:
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert re.findall(r"image:\s*(pdal/pdal:\S+)", compose) == [PDAL_IMAGE]


def test_refuses_a_scratch_folder_inside_the_checkout(tmp_path: Path) -> None:
    root, checkout = tmp_path / "snapshots", tmp_path / "repo"
    with pytest.raises(SnapshotError, match="checkout"):
        scratch_folder(root, checkout / "scratch", "ept-test-dsm", checkout=checkout)
    assert not (checkout / "scratch").exists()


def test_the_version_check_logs_its_begin_and_end() -> None:
    with capture_logs() as logs:
        assert check_pdal_version(FakeMake()) == PDAL_VERSION
    events = [entry["event"] for entry in logs]
    assert events == ["check_pdal_begin", "check_pdal_end"]
    assert logs[1]["duration_s"] >= 0
