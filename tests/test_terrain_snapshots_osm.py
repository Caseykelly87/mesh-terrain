"""The OpenStreetMap buildings and water command.

The download side uses a fake web server and a fake reader. read_layers runs
for real on the small Helsinki extract that ships inside pyrosm, so the
pyrosm call is exercised without the network.
"""

import hashlib
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import geopandas as gpd
import pyrosm
import pytest
from shapely.geometry import Point, box
from terrain_snapshot_fakes import FakeWeb

from pipeline.common.snapshot import SnapshotError, read_manifest, verify_manifest
from pipeline.terrain_snapshots.osm import (
    MSA_BBOX,
    OSM_ATTRIBUTION,
    BBox,
    build_osm,
    osm_snapshot_id,
    read_layers,
)

BASE = "https://example.test/north-america/us/"
EXTRACT = date(2026, 11, 1)
HELSINKI = Path(pyrosm.__file__).parent / "data" / "Helsinki.osm.pbf"


def _serve_region(web: FakeWeb, region: str, body: bytes, *, md5: str | None = None) -> None:
    name = f"{region}-261101.osm.pbf"
    web.serve(f"{BASE}{name}", body)
    digest = md5 or hashlib.md5(body).hexdigest()
    web.serve(f"{BASE}{name}.md5", f"{digest}  {name}\n".encode())


def _fake_reader(paths: Sequence[Path], bbox: BBox) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    assert [path.name for path in paths] == ["missouri-261101.osm.pbf", "illinois-261101.osm.pbf"]
    buildings = gpd.GeoDataFrame(
        {"id": [1, 2], "osm_type": ["way", "way"], "building": ["yes", "house"]},
        geometry=[box(-90.4, 38.7, -90.39, 38.71), box(-90.3, 38.6, -90.29, 38.61)],
        crs="EPSG:4326",
    )
    water = gpd.GeoDataFrame(
        {"id": [3], "osm_type": ["relation"], "kind": ["natural=water"]},
        geometry=[Point(-90.2, 38.8).buffer(0.01)],
        crs="EPSG:4326",
    )
    return buildings, water


def test_snapshot_holds_both_extracts_and_the_two_layers(tmp_path: Path) -> None:
    web = FakeWeb()
    _serve_region(web, "missouri", b"mo-pbf")
    _serve_region(web, "illinois", b"il-pbf")
    folder = build_osm(tmp_path, EXTRACT, opener=web, reader=_fake_reader, base_url=BASE)
    assert folder.name == "osm-geofabrik-mo-il-2026-11-01"
    assert verify_manifest(folder) == []
    manifest = read_manifest(folder)
    assert manifest["capture_date"] == "2026-11-01"
    assert manifest["licence"] == "Open Database License (ODbL) 1.0"
    assert manifest["attribution"] == OSM_ATTRIBUTION == "© OpenStreetMap contributors"
    assert manifest["source_url"] == [
        f"{BASE}missouri-261101.osm.pbf",
        f"{BASE}illinois-261101.osm.pbf",
    ]
    assert manifest["bbox_epsg4326"] == list(MSA_BBOX)
    assert manifest["layers"] == {"buildings.parquet": 2, "water.parquet": 1}
    md5s = [record["md5"] for record in manifest["source_objects"]]
    assert md5s == [hashlib.md5(b"mo-pbf").hexdigest(), hashlib.md5(b"il-pbf").hexdigest()]
    water = gpd.read_parquet(folder / "water.parquet")
    assert water.crs.to_epsg() == 4326


def test_refuses_an_extract_whose_md5_differs_from_geofabrik(tmp_path: Path) -> None:
    web = FakeWeb()
    _serve_region(web, "missouri", b"mo-pbf", md5="0" * 32)
    _serve_region(web, "illinois", b"il-pbf")
    with pytest.raises(SnapshotError, match="md5"):
        build_osm(tmp_path, EXTRACT, opener=web, reader=_fake_reader, base_url=BASE)
    assert not (tmp_path / osm_snapshot_id(EXTRACT)).exists()


def test_refuses_a_date_that_is_not_the_first_of_a_month(tmp_path: Path) -> None:
    web = FakeWeb()
    with pytest.raises(SnapshotError, match="first of a month"):
        build_osm(tmp_path, date(2026, 11, 14), opener=web, reader=_fake_reader, base_url=BASE)
    assert web.requests == []


def test_read_layers_keeps_polygons_only_and_drops_repeats_across_extracts() -> None:
    bbox: BBox = (24.93, 60.16, 24.96, 60.18)
    once, water_once = read_layers([HELSINKI], bbox)
    twice, water_twice = read_layers([HELSINKI, HELSINKI], bbox)
    assert len(once) > 100
    assert len(twice) == len(once)
    assert len(water_twice) == len(water_once)
    for layer in (once, water_once):
        assert layer.crs.to_epsg() == 4326
        assert set(layer.geom_type) <= {"Polygon", "MultiPolygon"}
    assert list(once.columns) == ["id", "osm_type", "building", "geometry"]
    assert list(water_once.columns) == ["id", "osm_type", "kind", "geometry"]
    keys = list(zip(once["osm_type"], once["id"], strict=True))
    assert keys == sorted(keys)
    assert once.index.tolist() == list(range(len(once)))
