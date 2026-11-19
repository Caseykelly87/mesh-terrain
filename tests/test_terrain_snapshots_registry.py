"""Registering terrain snapshots, and the DSM's check that its OSM snapshot is registered.

The DSM refuses an OSM snapshot without a snapshot row whose manifest_sha256
matches the folder (Casey: "OpenStreetMap water polygons from the registered
snapshot").

The database tests register into a throwaway schema built from the raw DDL
(tests/raw_schema.py), inside a transaction that is rolled back, never into the
real raw tables (Casey, issue #55 comment 6961809084, item 10), and skip where
no warehouse answers, as in CI's tests job. The fixture's lock check fails a
test whose registry call reached a real raw table. The branch that creates the
snapshot table when it is missing, and the SQL each function sends, are tested
with a fake connection.
"""

import inspect
import re
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import geopandas as gpd
import pytest
from psycopg import sql
from raw_schema import ThrowawayRaw, throwaway_raw_schema
from structlog.testing import capture_logs

from pipeline.common.snapshot import (
    MANIFEST_NAME,
    SnapshotError,
    finish_snapshot,
    register_verified,
    sha256_file,
    start_snapshot,
    write_manifest,
)
from pipeline.terrain_snapshots import registry
from pipeline.terrain_snapshots.dsm import OsmSnapshotMissingError, Registered, osm_water
from pipeline.terrain_snapshots.registry import (
    lookup_registered,
    raw_snapshot_exists,
    register_snapshot,
    registered_manifest_sha256,
)

OSM_ID = "osm-geofabrik-mo-il-1999-01-01"
THROWAWAY = "test_load_000000000000"
# A schema-qualified name, quoted or not: the schema is group 1.
QUALIFIED = re.compile(r'(?<![\w$"])"?([A-Za-z_][\w$]*)"?\s*\.\s*"?[A-Za-z_]')
SQL_COMMENT = re.compile(r"--[^\n]*")


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
def raw() -> Iterator[ThrowawayRaw]:
    with throwaway_raw_schema() as throwaway:
        yield throwaway


def test_register_writes_one_row_and_is_idempotent(raw: ThrowawayRaw, tmp_path: Path) -> None:
    folder = _osm_folder(tmp_path)
    assert register_snapshot(raw.conn, tmp_path, OSM_ID, schema=raw.schema) == 1
    assert register_snapshot(raw.conn, tmp_path, OSM_ID, schema=raw.schema) == 0
    assert raw_snapshot_exists(raw.conn, schema=raw.schema)
    found = registered_manifest_sha256(raw.conn, OSM_ID, schema=raw.schema)
    assert found == sha256_file(folder / MANIFEST_NAME)


def test_an_unregistered_id_has_no_row(raw: ThrowawayRaw) -> None:
    assert registered_manifest_sha256(raw.conn, "osm-never-registered", schema=raw.schema) is None


def test_register_refuses_a_partial_or_missing_snapshot(raw: ThrowawayRaw, tmp_path: Path) -> None:
    start_snapshot(tmp_path, "half-done")
    with pytest.raises(SnapshotError):
        register_snapshot(raw.conn, tmp_path, "half-done", schema=raw.schema)
    with pytest.raises(SnapshotError):
        register_snapshot(raw.conn, tmp_path, "absent", schema=raw.schema)


def _registered_in(raw: ThrowawayRaw) -> Registered:
    """The DSM's registration lookup, reading the throwaway schema on the test's connection."""
    return lambda snapshot_id: registered_manifest_sha256(raw.conn, snapshot_id, schema=raw.schema)


def test_the_dsm_accepts_a_registered_osm_snapshot(raw: ThrowawayRaw, tmp_path: Path) -> None:
    _osm_folder(tmp_path)
    register_snapshot(raw.conn, tmp_path, OSM_ID, schema=raw.schema)
    water, osm = osm_water(tmp_path, OSM_ID, _registered_in(raw))
    assert water.empty
    assert osm["id"] == OSM_ID


def test_the_dsm_refuses_an_unregistered_osm_snapshot(raw: ThrowawayRaw, tmp_path: Path) -> None:
    _osm_folder(tmp_path)
    with pytest.raises(OsmSnapshotMissingError, match="not registered"):
        osm_water(tmp_path, OSM_ID, _registered_in(raw))


def test_the_dsm_refuses_a_row_whose_manifest_differs(raw: ThrowawayRaw, tmp_path: Path) -> None:
    # Register one folder, then read a different folder with the same id.
    _osm_folder(tmp_path / "a")
    register_snapshot(raw.conn, tmp_path / "a", OSM_ID, schema=raw.schema)
    _osm_folder(tmp_path / "b", licence="ODbL 1.0")
    with pytest.raises(OsmSnapshotMissingError, match="manifest_sha256"):
        osm_water(tmp_path / "b", OSM_ID, _registered_in(raw))


def test_the_lookup_logs_a_begin_and_an_end(raw: ThrowawayRaw) -> None:
    # The lookup opens its own connection, which cannot see the throwaway
    # schema the test's transaction created, so it finds no table and no row.
    with capture_logs() as logs:
        assert lookup_registered("osm-never-registered", schema=raw.schema) is None
    assert [entry["event"] for entry in logs] == ["registry_lookup_begin", "registry_lookup_end"]
    assert logs[1]["registered"] is False
    assert logs[0]["schema"] == logs[1]["schema"] == raw.schema


class _FakeConn:
    """Answers every query with the row (exists,) and records each statement with its parameters."""

    def __init__(self, exists: bool) -> None:
        self.exists = exists
        self.rowcount = 1
        self.statements: list[tuple[str, Any]] = []

    def execute(self, query: str | sql.Composable, params: Any = None) -> "_FakeConn":
        text = query if isinstance(query, str) else query.as_string(None)
        self.statements.append((text, params))
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
    monkeypatch.setattr(registry, "apply_ddl", lambda conn, **kwargs: ddl.append(kwargs))
    monkeypatch.setattr(registry, "register_verified", lambda conn, folder, **kwargs: 1)
    assert register_snapshot(_FakeConn(exists), tmp_path, OSM_ID, schema=THROWAWAY) == 1
    assert ddl == [{"schema": THROWAWAY}] * ddl_runs


def _schemas_named(statements: list[tuple[str, Any]]) -> set[str]:
    """Every schema a statement qualifies a name with, outside SQL comments."""
    texts = (SQL_COMMENT.sub("", text) for text, _ in statements)
    return {name for text in texts for name in QUALIFIED.findall(text)}


def test_every_registry_statement_names_only_the_schema_it_is_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _osm_folder(tmp_path)
    missing, present, own = _FakeConn(False), _FakeConn(True), _FakeConn(True)
    register_snapshot(missing, tmp_path, OSM_ID, schema=THROWAWAY)
    register_snapshot(present, tmp_path, OSM_ID, schema=THROWAWAY)
    registered_manifest_sha256(present, OSM_ID, schema=THROWAWAY)
    monkeypatch.setattr(registry, "connect", lambda: nullcontext(own))
    lookup_registered(OSM_ID, schema=THROWAWAY)
    statements = missing.statements + present.statements + own.statements
    assert _schemas_named(statements) == {THROWAWAY}
    lookups = [params for text, params in statements if "to_regclass" in text]
    assert lookups == [(THROWAWAY,)] * 4


@pytest.mark.parametrize(
    "function",
    [raw_snapshot_exists, register_snapshot, registered_manifest_sha256, lookup_registered],
)
def test_each_function_takes_a_keyword_schema_defaulting_as_registration_does(
    function: Any,
) -> None:
    # The registry writes and reads where register_verified writes unless told otherwise.
    parameter = inspect.signature(function).parameters["schema"]
    shared = inspect.signature(register_verified).parameters["schema"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default == shared.default
