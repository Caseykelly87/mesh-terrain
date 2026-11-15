"""Snapshot folders for any input: lifecycle, manifest, verification and registration (snp_01).

The snapshot here is an invented elevation tile, not an MQTT capture, so the
tests show that nothing in the shared module needs the capture's fields. The
database test runs inside a transaction that is rolled back and is skipped
where no warehouse answers, as in CI's tests job.
"""

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from structlog.testing import capture_logs

from ingester.load import apply_ddl
from pipeline.common import snapshot as snapshot_module
from pipeline.common.settings import get_settings
from pipeline.common.snapshot import (
    MANIFEST_NAME,
    REQUIRED_METADATA,
    PartialExistsError,
    SnapshotError,
    SnapshotExistsError,
    finish_snapshot,
    finished_snapshots,
    partial_folders,
    read_manifest,
    register,
    register_verified,
    sha256_file,
    snapshot_folder,
    start_snapshot,
    verified_manifest,
    verify_manifest,
    write_manifest,
)

SNAPSHOT_ID = "example-dem-tile-2026-01-01"
METADATA: dict[str, Any] = {
    "snapshot_id": SNAPSHOT_ID,
    "capture_date": "2026-01-01",
    "source_url": "https://elevation.example.invalid/tiles/example.tif",
    "licence": "Invented licence line for a test.",
    "seeds": {},
}
SOURCE = Path(snapshot_module.__file__).read_text(encoding="utf-8")


class RecordingConnection:
    """Stands in for a psycopg connection and records each statement it is sent."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple[object, ...]]] = []
        self.rowcount = 1

    def execute(self, query: str, params: tuple[object, ...]) -> "RecordingConnection":
        self.statements.append((query, params))
        return self


def fill(folder: Path) -> None:
    (folder / "tile").mkdir()
    (folder / "tile" / "example.tif").write_bytes(b"rows")
    (folder / "README.md").write_text("what, when, where\n", encoding="utf-8")


def finished(root: Path, metadata: dict[str, Any] | None = None) -> Path:
    partial = start_snapshot(root, SNAPSHOT_ID)
    fill(partial)
    write_manifest(partial, metadata or METADATA)
    return finish_snapshot(partial)


def test_start_creates_the_partial_folder(tmp_path: Path) -> None:
    partial = start_snapshot(tmp_path, SNAPSHOT_ID)
    assert partial == tmp_path / f"{SNAPSHOT_ID}.partial"
    assert partial.is_dir()


def test_start_refuses_when_the_snapshot_exists(tmp_path: Path) -> None:
    (tmp_path / SNAPSHOT_ID).mkdir()
    with pytest.raises(SnapshotExistsError):
        start_snapshot(tmp_path, SNAPSHOT_ID)


def test_start_refuses_and_keeps_an_earlier_partial_folder(tmp_path: Path) -> None:
    # Nothing deletes a .partial folder automatically; Casey decides (snp_01).
    earlier = tmp_path / f"{SNAPSHOT_ID}.partial"
    earlier.mkdir()
    (earlier / "left-over").write_bytes(b"x")
    with pytest.raises(PartialExistsError):
        start_snapshot(tmp_path, SNAPSHOT_ID)
    assert (earlier / "left-over").exists()


@pytest.mark.parametrize("bad", ["", "x.partial", "../escape", "a/b", "Upper", "-lead"])
def test_start_rejects_bad_ids(tmp_path: Path, bad: str) -> None:
    with pytest.raises(SnapshotError):
        start_snapshot(tmp_path, bad)


def test_the_manifest_round_trips_the_metadata_and_the_file_list(tmp_path: Path) -> None:
    manifest = read_manifest(finished(tmp_path))
    assert {key: manifest[key] for key in METADATA} == METADATA
    assert [entry["path"] for entry in manifest["files"]] == ["README.md", "tile/example.tif"]
    tile = manifest["files"][1]
    assert tile["sha256"] == "bc51e9e65d79e1c90ba544ab2eae1044ccdf8c1af7a559ba375cba4276f7d99e"
    assert tile["bytes"] == 4
    assert manifest["snapshot_bytes"] == sum(entry["bytes"] for entry in manifest["files"])
    assert "written_at" in manifest


def test_the_manifest_is_sorted_json_ending_in_a_newline(tmp_path: Path) -> None:
    text = (finished(tmp_path) / MANIFEST_NAME).read_text(encoding="utf-8")
    assert text.endswith("}\n")
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"


def test_the_shared_manifest_requires_only_the_snp_01_fields() -> None:
    assert REQUIRED_METADATA == ("snapshot_id", "capture_date", "source_url", "licence", "seeds")


@pytest.mark.parametrize("field", REQUIRED_METADATA)
def test_the_manifest_refuses_metadata_without_a_snp_01_field(tmp_path: Path, field: str) -> None:
    partial = start_snapshot(tmp_path, SNAPSHOT_ID)
    incomplete = {key: value for key, value in METADATA.items() if key != field}
    with pytest.raises(SnapshotError):
        write_manifest(partial, incomplete)
    assert not (partial / MANIFEST_NAME).exists()


def test_the_manifest_refuses_a_different_snapshot_id(tmp_path: Path) -> None:
    partial = start_snapshot(tmp_path, SNAPSHOT_ID)
    with pytest.raises(SnapshotError):
        write_manifest(partial, {**METADATA, "snapshot_id": "another-2026-01-01"})


def test_a_caller_can_require_more_fields(tmp_path: Path) -> None:
    partial = start_snapshot(tmp_path, SNAPSHOT_ID)
    with pytest.raises(SnapshotError):
        write_manifest(partial, METADATA, required=(*REQUIRED_METADATA, "resolution_m"))


def test_a_caller_check_sees_the_files_and_can_refuse(tmp_path: Path) -> None:
    partial = start_snapshot(tmp_path, SNAPSHOT_ID)
    fill(partial)
    seen: list[str] = []

    def refuse(folder: Path, files: list[Path]) -> dict[str, object]:
        seen.extend(path.relative_to(folder).as_posix() for path in files)
        raise SnapshotError("not a tile")

    with pytest.raises(SnapshotError):
        write_manifest(partial, METADATA, check=refuse)
    assert seen == ["README.md", "tile/example.tif"]
    assert not (partial / MANIFEST_NAME).exists()


def test_a_caller_check_adds_to_the_end_log(tmp_path: Path) -> None:
    partial = start_snapshot(tmp_path, SNAPSHOT_ID)
    fill(partial)
    with capture_logs() as logs:
        write_manifest(partial, METADATA, check=lambda folder, files: {"tiles": 1})
    end = next(entry for entry in logs if entry["event"] == "manifest_write_end")
    assert end["tiles"] == 1


def test_finish_renames_only_after_the_manifest(tmp_path: Path) -> None:
    partial = start_snapshot(tmp_path, SNAPSHOT_ID)
    fill(partial)
    with pytest.raises(SnapshotError):
        finish_snapshot(partial)
    write_manifest(partial, METADATA)
    final = finish_snapshot(partial)
    assert final == tmp_path / SNAPSHOT_ID
    assert (final / MANIFEST_NAME).exists()
    assert not partial.exists()


def test_finish_refuses_a_folder_that_is_not_partial(tmp_path: Path) -> None:
    with pytest.raises(SnapshotError):
        finish_snapshot(finished(tmp_path))


def test_finish_never_overwrites_a_snapshot(tmp_path: Path) -> None:
    partial = start_snapshot(tmp_path, SNAPSHOT_ID)
    fill(partial)
    write_manifest(partial, METADATA)
    (tmp_path / SNAPSHOT_ID).mkdir()
    (tmp_path / SNAPSHOT_ID / "earlier").write_bytes(b"x")
    with pytest.raises(SnapshotExistsError):
        finish_snapshot(partial)
    assert (tmp_path / SNAPSHOT_ID / "earlier").read_bytes() == b"x"
    assert partial.is_dir()


def test_verify_passes_on_an_untouched_snapshot(tmp_path: Path) -> None:
    assert verify_manifest(finished(tmp_path)) == []


def test_verify_names_changed_missing_and_extra_files(tmp_path: Path) -> None:
    final = finished(tmp_path)
    (final / "tile" / "example.tif").write_bytes(b"changed")
    (final / "README.md").unlink()
    (final / "extra.tif").write_bytes(b"x")
    assert verify_manifest(final) == [
        "changed: tile/example.tif",
        "extra: extra.tif",
        "missing: README.md",
    ]


def test_verify_refuses_a_folder_without_a_manifest(tmp_path: Path) -> None:
    partial = start_snapshot(tmp_path, SNAPSHOT_ID)
    with pytest.raises(SnapshotError):
        verify_manifest(partial)


def test_verified_manifest_raises_on_a_mismatch(tmp_path: Path) -> None:
    final = finished(tmp_path)
    assert verified_manifest(final)["snapshot_id"] == SNAPSHOT_ID
    (final / "tile" / "example.tif").write_bytes(b"changed")
    with pytest.raises(SnapshotError, match="differ from the manifest"):
        verified_manifest(final)


def test_partial_and_finished_folders_are_listed_apart(tmp_path: Path) -> None:
    finished(tmp_path)
    start_snapshot(tmp_path, "example-dem-tile-2026-01-02")
    assert partial_folders(tmp_path) == [tmp_path / "example-dem-tile-2026-01-02.partial"]
    assert finished_snapshots(tmp_path) == [tmp_path / SNAPSHOT_ID]


def test_snapshot_folder_finds_a_finished_snapshot(tmp_path: Path) -> None:
    assert snapshot_folder(tmp_path, SNAPSHOT_ID) == finished(tmp_path)


def test_snapshot_folder_refuses_a_partial_folder(tmp_path: Path) -> None:
    start_snapshot(tmp_path, SNAPSHOT_ID)
    with pytest.raises(SnapshotError):
        snapshot_folder(tmp_path, SNAPSHOT_ID)
    with pytest.raises(SnapshotError):
        snapshot_folder(tmp_path, f"{SNAPSHOT_ID}.partial")


def test_snapshot_folder_refuses_a_folder_without_a_manifest(tmp_path: Path) -> None:
    (tmp_path / SNAPSHOT_ID).mkdir()
    with pytest.raises(SnapshotError):
        snapshot_folder(tmp_path, SNAPSHOT_ID)


def test_filesystem_operations_log_start_and_end_with_the_snapshot_id(tmp_path: Path) -> None:
    with capture_logs() as logs:
        verify_manifest(finished(tmp_path))
    events = [(entry["event"], entry.get("snapshot_id")) for entry in logs]
    for name in ("snapshot_start", "manifest_write", "snapshot_finish", "manifest_verify"):
        assert (f"{name}_begin", SNAPSHOT_ID) in events
        assert (f"{name}_end", SNAPSHOT_ID) in events
    manifest_end = next(entry for entry in logs if entry["event"] == "manifest_write_end")
    assert manifest_end["files"] == 2
    assert manifest_end["bytes"] > 0
    assert manifest_end["duration_s"] >= 0


def test_register_sends_the_snp_01_fields(tmp_path: Path) -> None:
    final = finished(tmp_path)
    conn = RecordingConnection()
    assert register(conn, final, read_manifest(final)) == 1
    [(query, params)] = conn.statements
    assert "on conflict (snapshot_id) do nothing" in query
    assert params == (
        SNAPSHOT_ID,
        "2026-01-01",
        METADATA["source_url"],
        METADATA["licence"],
        str(final / MANIFEST_NAME),
        sha256_file(final / MANIFEST_NAME),
    )


def test_register_joins_a_list_of_source_urls(tmp_path: Path) -> None:
    urls = ["https://one.example.invalid/a.tif", "https://two.example.invalid/b.tif"]
    final = finished(tmp_path, {**METADATA, "source_url": urls})
    conn = RecordingConnection()
    register(conn, final, read_manifest(final))
    assert conn.statements[0][1][2] == ", ".join(urls)


def test_a_partial_folder_is_never_registered(tmp_path: Path) -> None:
    partial = start_snapshot(tmp_path, SNAPSHOT_ID)
    fill(partial)
    write_manifest(partial, METADATA)
    conn = RecordingConnection()
    with pytest.raises(SnapshotError):
        register(conn, partial, read_manifest(partial))
    with pytest.raises(SnapshotError):
        register_verified(conn, partial)
    assert conn.statements == []


def test_a_manifest_mismatch_stops_registration(tmp_path: Path) -> None:
    final = finished(tmp_path)
    (final / "tile" / "example.tif").write_bytes(b"changed")
    conn = RecordingConnection()
    with pytest.raises(SnapshotError, match="differ from the manifest"):
        register_verified(conn, final)
    assert conn.statements == []


def test_a_manifest_naming_another_snapshot_is_not_registered(tmp_path: Path) -> None:
    final = finished(tmp_path)
    conn = RecordingConnection()
    with pytest.raises(SnapshotError):
        register(conn, final, {**read_manifest(final), "snapshot_id": "another-2026-01-01"})
    assert conn.statements == []


def test_the_shared_module_never_updates_or_deletes() -> None:
    # ing_07 and snp_01: registration only ever inserts.
    assert not re.search(r"\b(update|delete|truncate|drop)\b", SOURCE, re.IGNORECASE)


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


def test_register_is_idempotent(conn: psycopg.Connection, tmp_path: Path) -> None:
    final = finished(tmp_path)
    apply_ddl(conn)
    assert register_verified(conn, final) == 1
    assert register_verified(conn, final) == 0
    row = conn.execute(
        "select capture_date::text, source_url, licence, manifest_sha256 "
        "from raw.snapshot where snapshot_id = %s",
        (SNAPSHOT_ID,),
    ).fetchall()
    assert row == [
        (
            "2026-01-01",
            METADATA["source_url"],
            METADATA["licence"],
            sha256_file(final / MANIFEST_NAME),
        )
    ]
