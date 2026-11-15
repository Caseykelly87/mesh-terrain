"""The canopy height command (Casey, unit 5b (c) C1) on a tiny EPSG:3857 tile."""

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from terrain_snapshot_fakes import FakeWeb, write_geotiff

from pipeline.common.snapshot import SnapshotError, read_manifest, verify_manifest
from pipeline.terrain_snapshots.canopy import TILE_0231113110, CanopySource, build_canopy
from pipeline.terrain_snapshots.remote import PinMismatchError, PinnedObject

TIF_URL = "https://example.test/chm/0000.tif"
DATES_URL = "https://example.test/metadata/0000.geojson"
TODAY = date(2026, 11, 15)
# A 100 m box of x72y429 in EPSG:26915 and a 3857 tile around it.
TARGET = (725_000.0, 4_285_000.0, 725_100.0, 4_285_100.0)


def _dates() -> bytes:
    features = [
        {"type": "Feature", "properties": {"acq_date": day}, "geometry": None}
        for day in ("2019-06-27", "2018-10-02", "2020-04-11")
    ]
    return json.dumps({"type": "FeatureCollection", "features": features}).encode()


def _source(
    web: FakeWeb, tmp_path: Path, *, epsg: int = 3857, dtype: str = "uint8"
) -> CanopySource:
    heights = np.arange(400, dtype=dtype).reshape(20, 20) % 30
    body = write_geotiff(
        tmp_path / "chm.tif", heights, epsg=epsg, west=-10_064_800.0, north=4_676_850.0, res=10.0
    )
    return CanopySource(
        snapshot_id="canopy-v2-test",
        tile=web.serve(TIF_URL, body),
        dates=web.serve(DATES_URL, _dates()),
        target_bounds_26915=TARGET,
    )


def test_keeps_tile_and_dates_with_checks_and_sha256_side_by_side(tmp_path: Path) -> None:
    web = FakeWeb()
    source = _source(web, tmp_path)
    folder = build_canopy(tmp_path / "snapshots", source=source, opener=web, today=TODAY)
    assert verify_manifest(folder) == []
    manifest = read_manifest(folder)
    tile, dates = manifest["source_objects"]
    assert tile["bytes"] == source.tile.bytes
    assert tile["etag"] == source.tile.etag
    assert tile["last_modified"] == source.tile.last_modified
    assert len(tile["sha256"]) == 64
    assert dates["url"] == DATES_URL
    assert "CC BY 4.0" in manifest["licence"] and "DINOv3" in manifest["licence"]
    assert manifest["crs"] == "EPSG:3857"
    assert manifest["acq_date_range"] == ["2018-10-02", "2020-04-11"]
    assert manifest["resampling"].startswith("nearest neighbour")
    assert manifest["covers_epsg26915"] == list(TARGET)


def test_refuses_a_tile_that_does_not_cover_the_target(tmp_path: Path) -> None:
    web = FakeWeb()
    source = replace(
        _source(web, tmp_path), target_bounds_26915=(730_000.0, 4_285_000.0, 730_100.0, 4_285_100.0)
    )
    with pytest.raises(SnapshotError, match="cover"):
        build_canopy(tmp_path / "snapshots", source=source, opener=web, today=TODAY)


def test_refuses_a_tile_that_is_not_whole_metres_in_web_mercator(tmp_path: Path) -> None:
    web = FakeWeb()
    with pytest.raises(SnapshotError, match="uint8"):
        build_canopy(
            tmp_path / "snapshots",
            source=_source(web, tmp_path, dtype="float32"),
            opener=web,
            today=TODAY,
        )


def test_refuses_before_download_when_the_tile_was_replaced(tmp_path: Path) -> None:
    web = FakeWeb()
    source = _source(web, tmp_path)
    source = replace(
        source, tile=replace(source.tile, last_modified="Thu, 01 Jan 2026 00:00:00 GMT")
    )
    with pytest.raises(PinMismatchError, match="last_modified"):
        build_canopy(tmp_path / "snapshots", source=source, opener=web, today=TODAY)
    assert web.downloads() == []


def test_the_real_pins_are_the_measured_ones() -> None:
    base = (
        "https://dataforgood-fb-data.s3.amazonaws.com/forests/v2/global/dinov3_global_chm_v2_ml3/"
    )
    assert TILE_0231113110.snapshot_id == "canopy-v2-0231113110"
    assert TILE_0231113110.tile == PinnedObject(
        url=f"{base}chm/0231113110.tif",
        bytes=220_436_367,
        etag='"bcabd5820b097fbac3911603d2ba875f-27"',
        last_modified="Wed, 07 Jan 2026 08:47:55 GMT",
    )
    assert TILE_0231113110.dates == PinnedObject(
        url=f"{base}metadata/0231113110.geojson",
        bytes=18_706_185,
        etag='"f6a60e0a8d17ad3a72a5296bff7559f9-3"',
        last_modified="Wed, 31 Dec 2025 12:07:31 GMT",
    )
    assert TILE_0231113110.target_bounds_26915 == (719_994.0, 4_279_994.0, 730_006.0, 4_290_006.0)
