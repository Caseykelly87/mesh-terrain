"""Snapshot folders for every input: `.partial` lifecycle, sha256 manifest, registration (snp_01).

A snapshot command writes its files into `<root>/<id>.partial/`, then calls
write_manifest, which lists every file with its sha256 and size beside the
metadata snp_01 asks for, then finish_snapshot, which renames the folder to
`<root>/<id>/`. Once the manifest exists the folder is final and nothing here
writes into it again (ing_02). verify_manifest compares a finished folder with
its manifest, and register records a finished snapshot in raw.snapshot with an
insert that ignores a row already there. Nothing here removes a file or a
folder: an existing `.partial` folder stops a new run and is named in the
error, and Casey decides what happens to it (ADR 0005 item 6).

What a snapshot holds and any check beyond the sha256 (the capture's hour
files and delivery keys, a raster's extent) belong to the command that writes
it, through write_manifest's `required` and `check` arguments. This module does
not download, decode or read the files' contents, and does not create the raw
schema: raw.snapshot comes from ingester/sql/raw_tables.sql. Every filesystem
step logs its start and its end with the snapshot id, the file count, the bytes
and the duration.
"""

import hashlib
import json
import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import structlog
from psycopg import sql

log = structlog.get_logger(component="pipeline.common.snapshot")

PARTIAL_SUFFIX = ".partial"
# The schema register writes to. Only tests pass another, a throwaway schema
# built from the same DDL (Casey, issue #55, item 10).
RAW_SCHEMA = "raw"
MANIFEST_NAME = "manifest.json"
SNAPSHOT_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]*")
# snp_01: an id, a capture date, a source URL and a licence, and the seed of
# every random draw; a snapshot that made no draw records an empty mapping.
REQUIRED_METADATA = ("snapshot_id", "capture_date", "source_url", "licence", "seeds")
# raw.snapshot's columns in table order, as ingester/sql/raw_tables.sql creates them.
SNAPSHOT_COLUMNS = (
    "snapshot_id",
    "capture_date",
    "source_url",
    "licence",
    "manifest_path",
    "manifest_sha256",
    "registered_at",
)
_CHUNK_BYTES = 1 << 20

ManifestCheck = Callable[[Path, list[Path]], Mapping[str, object]]
"""Called with the `.partial` folder and its files before hashing; raises to refuse."""


class SnapshotError(Exception):
    """A snapshot folder is not in the state an operation needs."""


class SnapshotExistsError(SnapshotError):
    """The final snapshot folder already exists; a snapshot is never overwritten."""


class PartialExistsError(SnapshotError):
    """A `.partial` folder from an earlier run exists; Casey decides what happens to it."""


@contextmanager
def _logged(step: str, snapshot_id: str, **context: str) -> Iterator[dict[str, Any]]:
    """Log the step's begin event, then its end event with what the block recorded.

    The snapshot id and any context fields are on both events.
    """
    bound = log.bind(snapshot_id=snapshot_id, **context)
    bound.info(f"{step}_begin")
    started = time.monotonic()
    details: dict[str, Any] = {}
    yield details
    bound.info(f"{step}_end", duration_s=round(time.monotonic() - started, 3), **details)


def snapshot_id_of(folder: Path) -> str:
    """The snapshot id of a final or `.partial` folder."""
    return folder.name.removesuffix(PARTIAL_SUFFIX)


def start_snapshot(root: Path, snapshot_id: str) -> Path:
    """Create `<root>/<id>.partial/` for a new run.

    Raises:
        SnapshotError: if the id is not lowercase letters, digits and hyphens.
        SnapshotExistsError: if `<root>/<id>/` exists.
        PartialExistsError: if `<root>/<id>.partial/` exists; the message names it.
    """
    if not SNAPSHOT_ID_PATTERN.fullmatch(snapshot_id):
        raise SnapshotError(f"snapshot id {snapshot_id!r} is not lowercase, digits and hyphens")
    final = root / snapshot_id
    partial = root / f"{snapshot_id}{PARTIAL_SUFFIX}"
    with _logged("snapshot_start", snapshot_id) as details:
        if final.exists():
            raise SnapshotExistsError(f"{final} exists; a refresh needs a new snapshot id")
        if partial.exists():
            raise PartialExistsError(
                f"{partial} exists from an earlier run; Casey deletes or keeps it by hand"
            )
        partial.mkdir(parents=True)
        details.update(path=str(partial), files=0, bytes=0)
    return partial


def sha256_file(path: Path) -> str:
    """Hex sha256 of a file, read in 1 MiB chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_files(folder: Path) -> list[Path]:
    """Every file of a snapshot folder except its own manifest, sorted by relative path."""
    manifest_path = folder / MANIFEST_NAME
    files = (path for path in folder.rglob("*") if path.is_file())
    return sorted(
        (path for path in files if path != manifest_path),
        key=lambda path: path.relative_to(folder).as_posix(),
    )


def check_metadata(
    partial: Path, metadata: Mapping[str, object], required: Sequence[str] = REQUIRED_METADATA
) -> None:
    """Refuse metadata that lacks a required field, has non-mapping seeds or names another snapshot.

    Raises:
        SnapshotError: naming the missing fields, the seeds, or the folder whose id differs.
    """
    missing = [name for name in required if name not in metadata]
    if missing:
        raise SnapshotError(f"manifest metadata lacks {', '.join(missing)}")
    if "seeds" in metadata and not isinstance(metadata["seeds"], Mapping):
        raise SnapshotError("manifest seeds must be a mapping of draw name to seed, {} for none")
    if f"{metadata['snapshot_id']}{PARTIAL_SUFFIX}" != partial.name:
        raise SnapshotError(f"metadata snapshot_id does not match folder {partial.name}")


def write_manifest(
    partial: Path,
    metadata: Mapping[str, object],
    *,
    required: Sequence[str] = REQUIRED_METADATA,
    check: ManifestCheck | None = None,
) -> Path:
    """Write `manifest.json` listing every file of a `.partial` folder.

    Args:
        partial: The run's `<id>.partial/` folder, README already written.
        metadata: At least the `required` fields; written as given.
        required: The fields the metadata must hold; the snp_01 ones by default.
        check: The writer's own check of its files, run before any hashing;
            it raises SnapshotError to refuse and returns details for the log.

    Returns:
        The manifest path.

    Raises:
        SnapshotError: if a field is missing, the id does not match the
            folder, or the check refuses.
    """
    check_metadata(partial, metadata, required)
    with _logged("manifest_write", snapshot_id_of(partial)) as details:
        files = snapshot_files(partial)
        if check is not None:
            details.update(check(partial, files))
        entries: list[dict[str, str | int]] = [
            {
                "path": path.relative_to(partial).as_posix(),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
            for path in files
        ]
        snapshot_bytes = sum(path.stat().st_size for path in files)
        manifest = {
            **metadata,
            "written_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "snapshot_bytes": snapshot_bytes,
            "files": entries,
        }
        path = partial / MANIFEST_NAME
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        details.update(path=str(path), files=len(entries), bytes=snapshot_bytes)
    return path


def finish_snapshot(partial: Path) -> Path:
    """Rename `<id>.partial/` to `<id>/` once its manifest exists.

    Raises:
        SnapshotError: if there is no manifest or the folder is not a `.partial` one.
        SnapshotExistsError: if the final folder appeared meanwhile.
    """
    if not partial.name.endswith(PARTIAL_SUFFIX):
        raise SnapshotError(f"{partial} is not a {PARTIAL_SUFFIX} folder")
    if not (partial / MANIFEST_NAME).is_file():
        raise SnapshotError(f"{partial} has no manifest; it stays partial")
    final = partial.with_name(snapshot_id_of(partial))
    with _logged("snapshot_finish", final.name) as details:
        if final.exists():
            raise SnapshotExistsError(f"{final} exists")
        renamed = partial.rename(final)
        files = snapshot_files(renamed)
        details.update(
            path=str(renamed), files=len(files), bytes=sum(p.stat().st_size for p in files)
        )
    return renamed


def _manifest_bytes(folder: Path) -> bytes:
    manifest_path = folder / MANIFEST_NAME
    if not manifest_path.is_file():
        raise SnapshotError(f"{folder} has no manifest")
    return manifest_path.read_bytes()


def read_manifest(folder: Path) -> dict[str, Any]:
    """The parsed manifest of a snapshot folder.

    Raises:
        SnapshotError: if the folder has no manifest.
    """
    manifest: dict[str, Any] = json.loads(_manifest_bytes(folder))
    return manifest


def verify_manifest(folder: Path) -> list[str]:
    """Compare a snapshot folder with its manifest.

    Returns:
        One line per problem, sorted: `changed: <path>`, `extra: <path>` or
        `missing: <path>`. Empty when every file matches.

    Raises:
        SnapshotError: if the folder has no manifest.
    """
    return _problems(folder, read_manifest(folder))


def _problems(folder: Path, manifest: Mapping[str, Any]) -> list[str]:
    with _logged("manifest_verify", snapshot_id_of(folder)) as details:
        expected = {entry["path"]: entry["sha256"] for entry in manifest["files"]}
        present = {path.relative_to(folder).as_posix(): path for path in snapshot_files(folder)}
        problems = [f"missing: {name}" for name in expected if name not in present]
        problems += [f"extra: {name}" for name in present if name not in expected]
        problems += [
            f"changed: {name}"
            for name, path in present.items()
            if name in expected and sha256_file(path) != expected[name]
        ]
        details.update(
            files=len(present),
            bytes=sum(path.stat().st_size for path in present.values()),
            problems=len(problems),
        )
    return sorted(problems)


def verified_manifest(folder: Path) -> dict[str, Any]:
    """The manifest of a snapshot folder whose every file matches it.

    Raises:
        SnapshotError: if the folder has no manifest or a file differs from it.
    """
    manifest = read_manifest(folder)
    problems = _problems(folder, manifest)
    if problems:
        raise SnapshotError(f"{len(problems)} files differ from the manifest: {problems[:3]}")
    return manifest


def partial_folders(root: Path) -> list[Path]:
    """Every `.partial` folder under root, for the run report."""
    return sorted(path for path in root.glob(f"*{PARTIAL_SUFFIX}") if path.is_dir())


def finished_snapshots(root: Path) -> list[Path]:
    """Every snapshot folder under root that has a manifest and is not partial."""
    return sorted(
        path
        for path in root.iterdir()
        if path.is_dir()
        and not path.name.endswith(PARTIAL_SUFFIX)
        and (path / MANIFEST_NAME).is_file()
    )


def snapshot_folder(root: Path, snapshot_id: str) -> Path:
    """The finished folder of a snapshot id.

    Raises:
        SnapshotError: for a `.partial` id or folder, or a folder with no manifest.
    """
    if snapshot_id.endswith(PARTIAL_SUFFIX):
        raise SnapshotError(f"{snapshot_id} is an unfinished run; it is never loaded")
    folder = root / snapshot_id
    if not (folder / MANIFEST_NAME).is_file():
        raise SnapshotError(f"{folder} has no manifest; only finished snapshots load")
    return folder


def _registry_values(folder: Path, manifest: Mapping[str, Any]) -> tuple[str, ...]:
    """The raw.snapshot values of a finished folder, hashing the manifest bytes it was read from."""
    if folder.name.endswith(PARTIAL_SUFFIX):
        raise SnapshotError(f"{folder} is an unfinished run; it is never registered")
    missing = [name for name in REQUIRED_METADATA if name not in manifest]
    if missing:
        raise SnapshotError(f"manifest lacks {', '.join(missing)}; it is not registered")
    if manifest["snapshot_id"] != folder.name:
        raise SnapshotError(f"manifest snapshot_id does not match folder {folder.name}")
    # Hash and compare the same bytes, so the stored sha256 belongs to the
    # manifest the caller verified, not to one written since.
    raw = _manifest_bytes(folder)
    if json.loads(raw) != manifest:
        raise SnapshotError(f"{folder / MANIFEST_NAME} changed since it was read")
    source_url = manifest["source_url"]
    return (
        manifest["snapshot_id"],
        manifest["capture_date"],
        ", ".join(source_url) if isinstance(source_url, list) else source_url,
        manifest["licence"],
        str(folder / MANIFEST_NAME),
        hashlib.sha256(raw).hexdigest(),
    )


def register(
    conn: psycopg.Connection,
    folder: Path,
    manifest: Mapping[str, Any],
    *,
    schema: str = RAW_SCHEMA,
) -> int:
    """Insert the raw.snapshot row of a finished snapshot.

    The caller verifies the folder against its manifest first (register_verified
    does) and owns the transaction. A list of source URLs is stored joined by
    ", ", since the column is text.

    Args:
        conn: An open warehouse connection where raw.snapshot exists.
        folder: The finished `<root>/<id>/` folder.
        manifest: Its parsed manifest, as read from the folder.
        schema: The schema holding the snapshot table; raw unless a test passes
            its throwaway schema.

    Returns:
        1 when the row is new, 0 when the snapshot id was already registered.

    Raises:
        SnapshotError: for a `.partial` folder, a folder without a manifest, a
            manifest that lacks a REQUIRED_METADATA field (snapshot_id,
            capture_date, source_url, licence or seeds), names another
            snapshot or differs from the file in the folder.
    """
    values = _registry_values(folder, manifest)
    with _logged("snapshot_register", values[0], schema=schema) as details:
        query = sql.SQL(
            "insert into {} "
            "(snapshot_id, capture_date, source_url, licence, manifest_path, manifest_sha256, "
            "registered_at) values (%s, %s, %s, %s, %s, %s, now()) "
            "on conflict (snapshot_id) do nothing"
        ).format(sql.Identifier(schema, "snapshot"))
        cursor = conn.execute(query, values)
        details["inserted"] = cursor.rowcount
    return cursor.rowcount


def register_verified(conn: psycopg.Connection, folder: Path, *, schema: str = RAW_SCHEMA) -> int:
    """Register a finished snapshot whose every file matches its manifest (ing_05, snp_01).

    Returns:
        1 when the row is new, 0 when the snapshot id was already registered.

    Raises:
        SnapshotError: if the folder is partial, has no manifest, a file
            differs from it, or register refuses the manifest.
    """
    if folder.name.endswith(PARTIAL_SUFFIX):
        raise SnapshotError(f"{folder} is an unfinished run; it is never registered")
    return register(conn, folder, verified_manifest(folder), schema=schema)
