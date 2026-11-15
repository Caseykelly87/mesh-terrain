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

from pipeline.common.snapshot import (
    SnapshotError,
    finish_snapshot,
    read_manifest,
    sha256_file,
    start_snapshot,
    verify_manifest,
    write_manifest,
)
from pipeline.terrain_snapshots.dsm import (
    PDAL_IMAGE,
    PDAL_VERSION,
    X72Y429_DSM,
    DsmSource,
    OsmSnapshotMissingError,
    build_dsm,
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
GRID = Grid(west=720_000.0, south=4_280_000.0, width=8, height=8)
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
        heights = (200.0 + writer["origin_x"] - 720_000.0 + cols).astype(np.float32)
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
    folder = build_dsm(root, OSM_ID, scratch, source=SOURCE, runner=make, today=TODAY)
    assert verify_manifest(folder) == []
    with rasterio.open(folder / "dsm.tif") as dsm:
        assert dsm.crs.to_epsg() == 26915
        assert (dsm.width, dsm.height, dsm.nodata) == (8, 8, -9999.0)
        assert tuple(dsm.bounds) == (720_000.0, 4_280_000.0, 720_008.0, 4_280_008.0)
        values = dsm.read(1)
    assert (values[:4, 4:] == -9999.0).all()
    assert values[7, 0] == 200.0 and values[7, 3] == 203.0
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
        build_dsm(root, OSM_ID, scratch, source=SOURCE, runner=make, today=TODAY)
    assert make.calls == []
    assert list(root.iterdir()) == []


def test_refuses_an_osm_snapshot_whose_files_changed(tmp_path: Path) -> None:
    root, scratch = _dirs(tmp_path)
    osm = _osm_snapshot(root, [])
    (osm / "water.parquet").write_bytes(b"changed")
    with pytest.raises(OsmSnapshotMissingError, match="differ"):
        build_dsm(root, OSM_ID, scratch, source=SOURCE, runner=FakeMake(), today=TODAY)


def test_a_thin_run_fails_and_leaves_no_finished_snapshot(tmp_path: Path) -> None:
    root, scratch = _dirs(tmp_path)
    _osm_snapshot(root, [])
    make = FakeMake(count=lambda row, col: 1.0 if row == col else 0.0)
    with pytest.raises(ThinOutputError):
        build_dsm(root, OSM_ID, scratch, source=SOURCE, runner=make, today=TODAY)
    assert not (root / SOURCE.snapshot_id).exists()


def test_refuses_another_pdal_version_before_starting(tmp_path: Path) -> None:
    root, scratch = _dirs(tmp_path)
    _osm_snapshot(root, [])
    make = FakeMake(version_output="pdal 2.9.2 (git-version: abc)\n")
    with pytest.raises(SnapshotError, match="2.9.2"):
        build_dsm(root, OSM_ID, scratch, source=SOURCE, runner=make, today=TODAY)
    assert not (root / f"{SOURCE.snapshot_id}.partial").exists()


def test_a_failed_pdal_run_stops_the_build(tmp_path: Path) -> None:
    root, scratch = _dirs(tmp_path)
    _osm_snapshot(root, [])
    with pytest.raises(SnapshotError, match="pdal failed"):
        build_dsm(root, OSM_ID, scratch, source=SOURCE, runner=FakeMake(returncode=1), today=TODAY)


def test_refuses_a_scratch_folder_inside_the_snapshots_folder(tmp_path: Path) -> None:
    root, _ = _dirs(tmp_path)
    _osm_snapshot(root, [])
    with pytest.raises(SnapshotError, match="scratch"):
        build_dsm(root, OSM_ID, root / "work", source=SOURCE, runner=FakeMake(), today=TODAY)


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
    assert (grid.west, grid.south, grid.width, grid.height) == (
        719_994.0,
        4_279_994.0,
        10_012,
        10_012,
    )
    windows = split(grid, X72Y429_DSM.windows_per_side)
    assert len(windows) == 16
    assert {(w.width, w.height) for w in windows} == {(2503, 2503)}
    assert sum(w.width * w.height for w in windows) == 10_012 * 10_012
    assert re.fullmatch(r"r\dc\d", windows[5].name)
