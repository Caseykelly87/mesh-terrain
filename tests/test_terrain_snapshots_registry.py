"""Registering terrain snapshots, and the DSM's check that its OSM snapshot is registered.

The DSM refuses an OSM snapshot without a raw.snapshot row whose manifest_sha256
matches the folder (Casey: "OpenStreetMap water polygons from the registered
snapshot").

The database tests run inside a transaction that is rolled back, and skip
where no warehouse answers, as in CI's tests job (the pattern of
tests/test_common_snapshot.py). The branch that creates raw.snapshot when it
is missing is tested with a fake, since no test drops a table.
"""

from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import geopandas as gpd
import psycopg
import pytest
from structlog.testing import capture_logs

from pipeline.common.settings import get_settings
from pipeline.common.snapshot import (
    MANIFEST_NAME,
    SnapshotError,
    finish_snapshot,
    sha256_file,
    start_snapshot,
    write_manifest,
)
from pipeline.terrain_snapshots import registry
from pipeline.terrain_snapshots.dsm import OsmSnapshotMissingError, osm_water
from pipeline.terrain_snapshots.registry import (
    lookup_registered,
    raw_snapshot_exists,
    register_snapshot,
    registered_manifest_sha256,
)

OSM_ID = "osm-geofabrik-mo-il-1999-01-01"


def _osm_folder(root: Path, snapshot_id: str = OSM_ID, licence: str = "ODbL") -> Path:
    partial = start_snapshot(root, snapshot_id)
    gpd.GeoDataFrame(
        {"id": [], "osm_type": [], "kind": []}, geometry=[], crs="EPSG:4326"
    ).to_parquet(partial / "water.parquet", index=False)
    metadata = {
        "snapshot_id": snapshot_id,
        "capture_date": "1999-01-01",
        "source_url": "https://example.test/x.osm.pbf",
        "licence": licence,
        "seeds": {},
    }
    write_manifest(partial, metadata)
    return finish_snapshot(partial)


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    settings = get_settings()
    try:
        connection = psycopg.connect(
            host=settings.warehouse_host,
            port=settings.warehouse_port,
            user=settings.warehouse_user,
            password=settings.warehouse_password.get_secret_value(),
            dbname=settings.warehouse_db,
            connect_timeout=3,
        )
    except psycopg.OperationalError as err:
        pytest.skip(f"no local warehouse ({err.__class__.__name__}); run make up")
    with connection, connection.transaction(force_rollback=True):
        yield connection


def test_register_writes_one_row_and_is_idempotent(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    folder = _osm_folder(tmp_path)
    assert register_snapshot(conn, tmp_path, OSM_ID) == 1
    assert register_snapshot(conn, tmp_path, OSM_ID) == 0
    assert raw_snapshot_exists(conn)
    assert registered_manifest_sha256(conn, OSM_ID) == sha256_file(folder / MANIFEST_NAME)


def test_an_unregistered_id_has_no_row(conn: psycopg.Connection) -> None:
    assert registered_manifest_sha256(conn, "osm-never-registered") is None


def test_register_refuses_a_partial_or_missing_snapshot(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    start_snapshot(tmp_path, "half-done")
    with pytest.raises(SnapshotError):
        register_snapshot(conn, tmp_path, "half-done")
    with pytest.raises(SnapshotError):
        register_snapshot(conn, tmp_path, "absent")


def test_the_dsm_accepts_a_registered_osm_snapshot(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    _osm_folder(tmp_path)
    register_snapshot(conn, tmp_path, OSM_ID)
    water, osm = osm_water(tmp_path, OSM_ID, lambda i: registered_manifest_sha256(conn, i))
    assert water.empty
    assert osm["id"] == OSM_ID


def test_the_dsm_refuses_an_unregistered_osm_snapshot(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    _osm_folder(tmp_path)
    with pytest.raises(OsmSnapshotMissingError, match="not registered"):
        osm_water(tmp_path, OSM_ID, lambda i: registered_manifest_sha256(conn, i))


def test_the_dsm_refuses_a_row_whose_manifest_differs(
    conn: psycopg.Connection, tmp_path: Path
) -> None:
    # Register one folder, then read a different folder with the same id.
    _osm_folder(tmp_path / "a")
    register_snapshot(conn, tmp_path / "a", OSM_ID)
    _osm_folder(tmp_path / "b", licence="ODbL 1.0")
    with pytest.raises(OsmSnapshotMissingError, match="manifest_sha256"):
        osm_water(tmp_path / "b", OSM_ID, lambda i: registered_manifest_sha256(conn, i))


class _FakeConn:
    """Answers the raw.snapshot existence query and records statements."""

    def __init__(self, exists: bool) -> None:
        self.exists = exists
        self.statements: list[str] = []

    def execute(self, query: str, params: Any = None) -> "_FakeConn":
        self.statements.append(query)
        return self

    def fetchone(self) -> tuple[bool]:
        return (self.exists,)

    def transaction(self) -> Any:
        return nullcontext()


@pytest.mark.parametrize(("exists", "ddl_runs"), [(True, 0), (False, 1)])
def test_ddl_runs_only_when_raw_snapshot_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, exists: bool, ddl_runs: int
) -> None:
    _osm_folder(tmp_path)
    ddl: list[object] = []
    monkeypatch.setattr(registry, "apply_ddl", ddl.append)
    monkeypatch.setattr(registry, "register_verified", lambda conn, folder: 1)
    assert register_snapshot(_FakeConn(exists), tmp_path, OSM_ID) == 1
    assert len(ddl) == ddl_runs


def test_the_lookup_logs_a_begin_and_an_end(conn: psycopg.Connection) -> None:
    # conn only makes the test skip where no warehouse answers; the lookup opens its own.
    with capture_logs() as logs:
        assert lookup_registered("osm-never-registered") is None
    assert [entry["event"] for entry in logs] == ["registry_lookup_begin", "registry_lookup_end"]
    assert logs[1]["registered"] is False
