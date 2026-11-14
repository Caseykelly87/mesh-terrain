"""Snapshot folders: the `.partial` lifecycle, file names and the sha256 manifest (snp_01).

A capture run or the reduction writes into `<root>/<id>.partial/`, writes the
README and the manifest last, then renames the folder to `<root>/<id>/`. The
manifest lists every file with its sha256 and size; once it exists the folder
is final and no code here writes into it again (ing_02). Nothing here deletes
anything: an existing `.partial` folder stops a new run and is named in the
error, and Casey deletes or keeps it by hand (ADR 0005 item 6). Every
filesystem step logs its start and its end with the snapshot id, the file
count, the bytes and the duration.
"""

import hashlib
import json
import re
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import structlog

log = structlog.get_logger(component="ingester.snapshot")

PARTIAL_SUFFIX = ".partial"
MANIFEST_NAME = "manifest.json"
SNAPSHOT_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]*")
FLUSH_MARKER = ".flush-"
DELIVERY_TABLE = "mqtt_message"
# snp_01: id, capture date, source URL, licence and seeds, and a data content
# hash per table for reproduction. ADR 0005 item 1: the run parameters and the
# stop reason ("reduction_complete" for the reduction, which has no END_AT or
# MAX_RAW_BYTES and records them as null). ing_03: deliveries received, stored,
# counted and merged per hour and broker.
REQUIRED_METADATA = (
    "snapshot_id",
    "capture_date",
    "source_url",
    "licence",
    "brokers",
    "topics",
    "end_at",
    "max_raw_bytes",
    "stop_reason",
    "seeds",
    "content_hashes",
    "deliveries",
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


@contextmanager
def _logged(step: str, snapshot_id: str) -> Iterator[dict[str, Any]]:
    """Log the step's begin event, then its end event with what the block recorded."""
    bound = log.bind(snapshot_id=snapshot_id)
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


def check_unique_delivery_keys(folder: Path) -> int:
    """Count the delivery rows of a snapshot folder, refusing a repeated delivery_key.

    ing_01 allows one row per delivery key in the whole snapshot, so the check
    spans every hour file, not one file at a time.

    Raises:
        SnapshotError: if a key appears more than once.
    """
    paths = sorted((folder / DELIVERY_TABLE).glob("*.parquet"))
    if not paths:
        return 0
    keys = pa.chunked_array(
        [pq.read_table(path, columns=["delivery_key"])["delivery_key"] for path in paths]
    )
    distinct = pc.count_distinct(keys).as_py()
    if distinct != len(keys):
        raise SnapshotError(f"{len(keys) - distinct} delivery keys repeat across the snapshot")
    return len(keys)


def write_manifest(partial: Path, metadata: Mapping[str, object]) -> Path:
    """Write `manifest.json` listing every file of a `.partial` folder.

    Args:
        partial: The run's `<id>.partial/` folder, README already written.
        metadata: At least the REQUIRED_METADATA fields; written as given.

    Returns:
        The manifest path.

    Raises:
        SnapshotError: if a field is missing, the id does not match the
            folder, a flush file has not been merged or a delivery key repeats.
    """
    _check_metadata(partial, metadata)
    with _logged("manifest_write", snapshot_id_of(partial)) as details:
        files = _snapshot_files(partial)
        unmerged = [path.name for path in files if FLUSH_MARKER in path.name]
        if unmerged:
            raise SnapshotError(f"{len(unmerged)} flush files are not merged")
        details["delivery_rows"] = check_unique_delivery_keys(partial)
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
        files = _snapshot_files(renamed)
        details.update(
            path=str(renamed), files=len(files), bytes=sum(p.stat().st_size for p in files)
        )
    return renamed


def read_manifest(folder: Path) -> dict[str, Any]:
    """The parsed manifest of a snapshot folder.

    Raises:
        SnapshotError: if the folder has no manifest.
    """
    manifest_path = folder / MANIFEST_NAME
    if not manifest_path.is_file():
        raise SnapshotError(f"{folder} has no manifest")
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    return manifest


def verify_manifest(folder: Path) -> list[str]:
    """Compare a snapshot folder with its manifest.

    Returns:
        One line per problem, sorted: `changed: <path>`, `extra: <path>` or
        `missing: <path>`. Empty when every file matches.

    Raises:
        SnapshotError: if the folder has no manifest.
    """
    manifest = read_manifest(folder)
    with _logged("manifest_verify", snapshot_id_of(folder)) as details:
        expected = {entry["path"]: entry["sha256"] for entry in manifest["files"]}
        present = {path.relative_to(folder).as_posix(): path for path in _snapshot_files(folder)}
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
