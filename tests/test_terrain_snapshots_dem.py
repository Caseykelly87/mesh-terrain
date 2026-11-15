"""The 1 m DEM tile command on a tiny GeoTIFF served by a fake web server."""

from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from terrain_snapshot_fakes import FakeWeb, write_geotiff

from pipeline.common.snapshot import (
    PartialExistsError,
    SnapshotError,
    SnapshotExistsError,
    read_manifest,
    verify_manifest,
)
from pipeline.terrain_snapshots.dem import X72Y429, DemSource, build_dem
from pipeline.terrain_snapshots.remote import PinMismatchError, PinnedObject

TIF_URL = "https://example.test/1m/TIFF/USGS_one_meter_x0y1_TEST.tif"
XML_URL = "https://example.test/1m/metadata/USGS_one_meter_x0y1_TEST_meta.xml"
META_XML = b"<metadata><idinfo><citation><citeinfo><pubdate>20200330</pubdate></citeinfo>"
TODAY = date(2026, 11, 15)


def _source(web: FakeWeb, tmp_path: Path, *, nodata_cells: int = 0, epsg: int = 26915) -> DemSource:
    heights = np.full((12, 12), 180.0, dtype=np.float32)
    heights.flat[:nodata_cells] = -999999.0
    body = write_geotiff(
        tmp_path / "src.tif",
        heights,
        epsg=epsg,
        west=719994.0,
        north=4280006.0,
        res=1.0,
        nodata=-999999.0,
    )
    return DemSource(
        snapshot_id="3dep-1m-test-x0y1",
        tile=web.serve(TIF_URL, body),
        metadata_xml=web.serve(XML_URL, META_XML),
        bounds=(719994.0, 4279994.0, 720006.0, 4280006.0),
        resolution_m=1.0,
        epsg=26915,
        tnm_project="TEST_2017",
    )


def test_writes_the_tile_and_its_metadata_with_a_verified_manifest(tmp_path: Path) -> None:
    web = FakeWeb()
    source = _source(web, tmp_path, nodata_cells=3)
    root = tmp_path / "snapshots"
    folder = build_dem(root, source=source, opener=web, today=TODAY)
    assert folder == root / "3dep-1m-test-x0y1"
    assert verify_manifest(folder) == []
    manifest = read_manifest(folder)
    assert manifest["capture_date"] == "2026-11-15"
    assert manifest["licence"] == "public domain (USGS 3DEP)"
    assert manifest["crs"] == "EPSG:26915"
    assert manifest["horizontal_datum"] == "NAD83(2011)"
    assert manifest["vertical_datum"].startswith("NAVD88")
    assert manifest["nodata_value"] == -999999.0
    assert manifest["nodata_pixels"] == 3
    assert manifest["tnm_publication_date"] == "2020-03-30"
    assert [record["url"] for record in manifest["source_objects"]] == [TIF_URL, XML_URL]
    assert all(len(record["sha256"]) == 64 for record in manifest["source_objects"])
    paths = {entry["path"] for entry in manifest["files"]}
    assert paths == {
        "README.md",
        "USGS_one_meter_x0y1_TEST.tif",
        "USGS_one_meter_x0y1_TEST_meta.xml",
    }


def test_refuses_a_tile_whose_crs_is_not_the_pinned_one(tmp_path: Path) -> None:
    web = FakeWeb()
    source = _source(web, tmp_path, epsg=6344)
    root = tmp_path / "snapshots"
    with pytest.raises(SnapshotError, match="EPSG"):
        build_dem(root, source=source, opener=web, today=TODAY)
    assert not (root / "3dep-1m-test-x0y1").exists()


def test_refuses_a_tile_whose_extent_differs(tmp_path: Path) -> None:
    web = FakeWeb()
    source = replace(_source(web, tmp_path), bounds=(719994.0, 4279994.0, 720007.0, 4280006.0))
    with pytest.raises(SnapshotError, match="bounds"):
        build_dem(tmp_path / "snapshots", source=source, opener=web, today=TODAY)


def test_refuses_before_download_when_the_tile_changed_upstream(tmp_path: Path) -> None:
    web = FakeWeb()
    source = _source(web, tmp_path)
    source = replace(source, tile=replace(source.tile, etag='"older"'))
    with pytest.raises(PinMismatchError):
        build_dem(tmp_path / "snapshots", source=source, opener=web, today=TODAY)
    assert web.downloads() == []


def test_never_overwrites_a_finished_or_partial_snapshot(tmp_path: Path) -> None:
    web = FakeWeb()
    source = _source(web, tmp_path)
    root = tmp_path / "snapshots"
    (root / source.snapshot_id).mkdir(parents=True)
    with pytest.raises(SnapshotExistsError):
        build_dem(root, source=source, opener=web, today=TODAY)
    (root / source.snapshot_id).rmdir()
    (root / f"{source.snapshot_id}.partial").mkdir()
    with pytest.raises(PartialExistsError):
        build_dem(root, source=source, opener=web, today=TODAY)
    assert web.requests == []


def test_the_x72y429_pins_match_the_measured_file() -> None:
    assert X72Y429.snapshot_id == "3dep-1m-mo-saintlouis-2017-x72y429"
    assert X72Y429.tile == PinnedObject(
        url="https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1m/Projects/"
        "MO_SaintLouis_2017/TIFF/USGS_one_meter_x72y429_MO_SaintLouis_2017.tif",
        bytes=483_356_883,
        etag='"7d4013fa90919a36b190a9e98ff8d13f-93"',
        last_modified="Fri, 13 Feb 2026 18:21:19 GMT",
    )
    assert X72Y429.bounds == (719994.0, 4279994.0, 730006.0, 4290006.0)
