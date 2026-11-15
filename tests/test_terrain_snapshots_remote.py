"""Pinned downloads: the header checks before a download and the hashes after it."""

import hashlib
from pathlib import Path

import pytest
from structlog.testing import capture_logs
from terrain_snapshot_fakes import FakeWeb

from pipeline.common.snapshot import start_snapshot
from pipeline.terrain_snapshots.common import build_snapshot, timed
from pipeline.terrain_snapshots.remote import (
    DownloadError,
    PinMismatchError,
    PinnedObject,
    check_pin,
    fetch_pinned,
    head,
    read_small,
)

URL = "https://example.test/data/tile.tif"
BODY = b"0123456789" * 1000


def test_head_reads_size_etag_and_date() -> None:
    web = FakeWeb()
    web.serve(URL, BODY, etag='"abc-2"')
    observed = head(URL, web)
    assert observed == PinnedObject(
        url=URL, bytes=len(BODY), etag='"abc-2"', last_modified="Fri, 13 Feb 2026 18:21:19 GMT"
    )
    assert web.requests == [("HEAD", URL)]


def test_check_pin_names_every_field_that_differs() -> None:
    pin = PinnedObject(url=URL, bytes=10, etag='"a"', last_modified="x")
    observed = PinnedObject(url=URL, bytes=11, etag='"b"', last_modified="x")
    with pytest.raises(PinMismatchError, match="bytes.*etag"):
        check_pin(pin, observed)


def test_check_pin_ignores_fields_the_pin_leaves_open() -> None:
    check_pin(PinnedObject(url=URL), PinnedObject(url=URL, bytes=5, etag='"z"'))


def test_fetch_refuses_before_downloading_when_the_etag_changed(tmp_path: Path) -> None:
    web = FakeWeb()
    pin = web.serve(URL, BODY)
    web.serve(URL, BODY, etag='"replaced-in-place"')
    with pytest.raises(PinMismatchError, match="etag"):
        fetch_pinned(pin, tmp_path / "tile.tif", web)
    assert web.downloads() == []
    assert not (tmp_path / "tile.tif").exists()


def test_fetch_records_the_checked_fields_and_the_sha256(tmp_path: Path) -> None:
    web = FakeWeb()
    pin = web.serve(URL, BODY)
    record = fetch_pinned(pin, tmp_path / "tile.tif", web)
    assert (tmp_path / "tile.tif").read_bytes() == BODY
    assert record == {
        "url": URL,
        "path": "tile.tif",
        "bytes": len(BODY),
        "etag": pin.etag,
        "last_modified": pin.last_modified,
        "sha256": hashlib.sha256(BODY).hexdigest(),
    }


def test_fetch_refuses_a_body_whose_sha256_differs_from_the_pin(tmp_path: Path) -> None:
    web = FakeWeb()
    web.serve(URL, BODY)
    pin = PinnedObject(url=URL, bytes=len(BODY), sha256="0" * 64)
    with pytest.raises(PinMismatchError, match="sha256"):
        fetch_pinned(pin, tmp_path / "tile.tif", web)


def test_a_short_body_is_refused(tmp_path: Path) -> None:
    web = FakeWeb()
    web.serve(URL, BODY, content_length=len(BODY) + 1)
    with pytest.raises(DownloadError, match="bytes"):
        fetch_pinned(PinnedObject(url=URL), tmp_path / "tile.tif", web)


def test_fetch_never_overwrites_a_file(tmp_path: Path) -> None:
    web = FakeWeb()
    pin = web.serve(URL, BODY)
    (tmp_path / "tile.tif").write_bytes(b"old")
    with pytest.raises(DownloadError, match="exists"):
        fetch_pinned(pin, tmp_path / "tile.tif", web)
    assert (tmp_path / "tile.tif").read_bytes() == b"old"


def test_timed_logs_a_start_and_an_end_with_the_duration() -> None:
    with capture_logs() as logs, timed("dem_snapshot", snapshot_id="x") as details:
        details["files"] = 2
    assert [entry["event"] for entry in logs] == ["dem_snapshot_begin", "dem_snapshot_end"]
    assert logs[1]["files"] == 2
    assert logs[1]["duration_s"] >= 0


def test_build_snapshot_refuses_an_existing_partial_and_writes_nothing(tmp_path: Path) -> None:
    start_snapshot(tmp_path, "demo")
    calls: list[Path] = []

    def fill(partial: Path) -> dict[str, object]:
        calls.append(partial)
        return {}

    with pytest.raises(Exception, match="earlier run"):
        build_snapshot(tmp_path, "demo", fill, {})
    assert calls == []


def test_head_and_read_small_log_a_begin_and_an_end() -> None:
    web = FakeWeb()
    web.serve(URL, BODY)
    with capture_logs() as logs:
        head(URL, web)
        read_small(URL, web, limit=len(BODY))
    events = [entry["event"] for entry in logs]
    assert events == ["http_head_begin", "http_head_end", "http_read_begin", "http_read_end"]
    assert all("duration_s" in entry for entry in logs[1::2])
