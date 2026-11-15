"""The ITU P.1812 map command on a fake zip; the result loads through terrain.p1812.load_maps."""

import hashlib
import io
import zipfile
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from terrain_snapshot_fakes import FakeWeb

from pipeline.common.snapshot import SnapshotError, read_manifest, verify_manifest
from pipeline.terrain_snapshots.itu_maps import (
    ITU_LICENCE,
    P1812_8_MAPS,
    ItuMapsSource,
    build_itu_maps,
)
from pipeline.terrain_snapshots.remote import PinMismatchError, PinnedObject
from terrain.p1812 import load_maps

URL = "https://example.test/rec/p/R-REC-P.1812-8-TEST!!ZIP-E.zip"
FIXTURE = Path(__file__).parent / "fixtures" / "snapshots" / "p1812-maps-synthetic"
TODAY = date(2026, 11, 15)


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def _source(web: FakeWeb, members: dict[str, bytes], **pins: str) -> ItuMapsSource:
    body = _zip(members)
    web.serve(URL, body)
    hashes = {name: hashlib.sha256(members[name]).hexdigest() for name in ("DN50.TXT", "N050.TXT")}
    hashes.update(pins)
    return ItuMapsSource(
        snapshot_id="itu-r-p1812-test",
        archive=PinnedObject(url=URL, bytes=len(body), sha256=hashlib.sha256(body).hexdigest()),
        archive_name="R-REC-P.1812-8-TEST.zip",
        members=hashes,
    )


def _maps() -> dict[str, bytes]:
    return {
        "ReadMe.doc": b"not read",
        "DN50.TXT": (FIXTURE / "DN50.TXT").read_bytes(),
        "N050.TXT": (FIXTURE / "N050.TXT").read_bytes(),
        "LAT.TXT": b"1",
    }


def test_keeps_the_zip_and_the_two_maps_and_loads_with_the_wrapper(tmp_path: Path) -> None:
    web = FakeWeb()
    source = _source(web, _maps())
    folder = build_itu_maps(tmp_path, source=source, opener=web, today=TODAY)
    assert verify_manifest(folder) == []
    names = sorted(path.name for path in folder.iterdir())
    assert names == ["DN50.TXT", "N050.TXT", "R-REC-P.1812-8-TEST.zip", "README.md", "manifest.json"]
    manifest = read_manifest(folder)
    assert manifest["licence"] == ITU_LICENCE
    assert manifest["source_url"] == URL
    assert manifest["source_objects"][0]["sha256"] == source.archive.sha256
    maps = load_maps(folder)
    assert maps.snapshot_id == "itu-r-p1812-test"
    assert maps.dn50.shape == (121, 241)


def test_never_builds_the_npz(tmp_path: Path) -> None:
    web = FakeWeb()
    folder = build_itu_maps(tmp_path, source=_source(web, _maps()), opener=web, today=TODAY)
    assert not list(folder.rglob("*.npz"))


def test_refuses_a_zip_whose_sha256_differs(tmp_path: Path) -> None:
    web = FakeWeb()
    source = _source(web, _maps())
    source = replace(source, archive=replace(source.archive, sha256="f" * 64))
    with pytest.raises(PinMismatchError, match="sha256"):
        build_itu_maps(tmp_path, source=source, opener=web, today=TODAY)
    assert not (tmp_path / "itu-r-p1812-test").exists()


def test_refuses_a_map_whose_sha256_differs(tmp_path: Path) -> None:
    web = FakeWeb()
    source = _source(web, _maps(), **{"N050.TXT": "e" * 64})
    with pytest.raises(SnapshotError, match="N050.TXT"):
        build_itu_maps(tmp_path, source=source, opener=web, today=TODAY)


def test_refuses_a_zip_without_a_map(tmp_path: Path) -> None:
    web = FakeWeb()
    members = _maps()
    source = _source(web, members)
    del members["DN50.TXT"]
    body = _zip(members)
    web.serve(URL, body)
    source = replace(
        source,
        archive=PinnedObject(url=URL, bytes=len(body), sha256=hashlib.sha256(body).hexdigest()),
    )
    with pytest.raises(SnapshotError, match="DN50.TXT"):
        build_itu_maps(tmp_path, source=source, opener=web, today=TODAY)


def test_the_real_pins_are_the_spike_checksums() -> None:
    assert P1812_8_MAPS.snapshot_id == "itu-r-p1812-8-202509"
    assert P1812_8_MAPS.archive.bytes == 635_849
    assert P1812_8_MAPS.archive.sha256 == (
        "d3a51f296e24f5d4f99daddcac8a38cb310fcb2081efef5d120f8f27bd8c5686"
    )
    assert P1812_8_MAPS.members["DN50.TXT"].startswith("4847bf95")
    assert P1812_8_MAPS.members["N050.TXT"].startswith("d27590ce")
    assert ITU_LICENCE.startswith("© ITU 2025. All rights reserved.")
