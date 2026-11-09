"""Snapshot folders: the `.partial` lifecycle, file names and the sha256 manifest (snp_01).

A capture run or the reduction writes into `<root>/<id>.partial/`, writes the
README and the manifest last, then renames the folder to `<root>/<id>/`. The
manifest lists every file with its sha256 and size; once it exists the folder
is final and no code here writes into it again (ing_02). Nothing here deletes
anything: an existing `.partial` folder stops a new run, and the run report
lists it for Casey to decide.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

PARTIAL_SUFFIX = ".partial"
MANIFEST_NAME = "manifest.json"
SNAPSHOT_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]*")
FLUSH_MARKER = ".flush-"
# snp_01: id, capture date, source URL, licence and seeds; ADR 0005 item 1:
# the stop reason; snp_01 reproduction: a data content hash per table.
REQUIRED_METADATA = (
    "snapshot_id",
    "capture_date",
    "source_url",
    "licence",
    "stop_reason",
    "seeds",
    "content_hashes",
    "decoder_version",
    "schema_version",
)
_CHUNK_BYTES = 1 << 20


class SnapshotError(Exception):
    """A snapshot folder is not in the state an operation needs."""


class SnapshotExistsError(SnapshotError):
    """The final snapshot folder already exists; a snapshot is never overwritten."""


class PartialExistsError(SnapshotError):
    """A `.partial` folder from an earlier run exists; Casey decides what happens to it."""


def start_snapshot(root: Path, snapshot_id: str) -> Path:
    """Create `<root>/<id>.partial/` for a new run.

    Raises:
        SnapshotError: if the id is not lowercase letters, digits and hyphens.
        SnapshotExistsError: if `<root>/<id>/` exists.
        PartialExistsError: if `<root>/<id>.partial/` exists.
    """
    if not SNAPSHOT_ID_PATTERN.fullmatch(snapshot_id):
        raise SnapshotError(f"snapshot id {snapshot_id!r} is not lowercase, digits and hyphens")
    final = root / snapshot_id
    partial = root / f"{snapshot_id}{PARTIAL_SUFFIX}"
    if final.exists():
        raise SnapshotExistsError(f"{final} exists; a refresh needs a new snapshot id")
    if partial.exists():
        raise PartialExistsError(f"{partial} exists from an earlier run; it is kept for review")
    partial.mkdir(parents=True)
    return partial


def _hour_stamp(hour: datetime) -> str:
    is_utc = hour.utcoffset() == timedelta(0)
    if not is_utc or (hour.minute, hour.second, hour.microsecond) != (0, 0, 0):
        raise ValueError("hour must be a whole UTC hour")
    return f"{hour:%Y-%m-%dT%H}"


def hour_file(folder: Path, table: str, hour: datetime) -> Path:
    """The merged file of one table and UTC hour: `<table>/<YYYY-MM-DDTHH>.parquet`."""
    return folder / table / f"{_hour_stamp(hour)}.parquet"


def flush_file(folder: Path, table: str, hour: datetime, sequence: int) -> Path:
    """One flush file of a table and hour, merged into the hour file at exit."""
    return folder / table / f"{_hour_stamp(hour)}{FLUSH_MARKER}{sequence:05d}.parquet"


def sha256_file(path: Path) -> str:
    """Hex sha256 of a file, read in 1 MiB chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_files(folder: Path) -> list[Path]:
    files = (path for path in folder.rglob("*") if path.is_file())
    return sorted(
        (path for path in files if path.name != MANIFEST_NAME),
        key=lambda path: path.relative_to(folder).as_posix(),
    )


def _check_metadata(partial: Path, metadata: Mapping[str, object]) -> None:
    missing = [name for name in REQUIRED_METADATA if name not in metadata]
    if missing:
        raise SnapshotError(f"manifest metadata lacks {', '.join(missing)}")
    if f"{metadata['snapshot_id']}{PARTIAL_SUFFIX}" != partial.name:
        raise SnapshotError(f"metadata snapshot_id does not match folder {partial.name}")


def write_manifest(partial: Path, metadata: Mapping[str, object]) -> Path:
    """Write `manifest.json` listing every file of a `.partial` folder.

    Args:
        partial: The run's `<id>.partial/` folder, README already written.
        metadata: At least the REQUIRED_METADATA fields; written as given.

    Returns:
        The manifest path.

    Raises:
        SnapshotError: if a field is missing, the id does not match the
            folder, or a flush file has not been merged.
    """
    _check_metadata(partial, metadata)
    files = _snapshot_files(partial)
    unmerged = [path.name for path in files if FLUSH_MARKER in path.name]
    if unmerged:
        raise SnapshotError(f"{len(unmerged)} flush files are not merged")
    entries: list[dict[str, str | int]] = [
        {
            "path": path.relative_to(partial).as_posix(),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in files
    ]
    manifest = {
        **metadata,
        "written_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "snapshot_bytes": sum(path.stat().st_size for path in files),
        "files": entries,
    }
    path = partial / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
    final = partial.with_name(partial.name.removesuffix(PARTIAL_SUFFIX))
    if final.exists():
        raise SnapshotExistsError(f"{final} exists")
    return partial.rename(final)


def verify_manifest(folder: Path) -> list[str]:
    """Compare a snapshot folder with its manifest.

    Returns:
        One line per problem, sorted: `changed: <path>`, `extra: <path>` or
        `missing: <path>`. Empty when every file matches.

    Raises:
        SnapshotError: if the folder has no manifest.
    """
    manifest_path = folder / MANIFEST_NAME
    if not manifest_path.is_file():
        raise SnapshotError(f"{folder} has no manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = {entry["path"]: entry["sha256"] for entry in manifest["files"]}
    present = {path.relative_to(folder).as_posix(): path for path in _snapshot_files(folder)}
    problems = [f"missing: {name}" for name in expected if name not in present]
    problems += [f"extra: {name}" for name in present if name not in expected]
    problems += [
        f"changed: {name}"
        for name, path in present.items()
        if name in expected and sha256_file(path) != expected[name]
    ]
    return sorted(problems)


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
