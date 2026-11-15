"""Capture snapshot folders: hour file names and the capture manifest's fields and checks (snp_01).

The `.partial` lifecycle, the sha256 manifest, verification and registration
are shared by every snapshot and live in pipeline.common.snapshot. This module
adds what a capture or reduction snapshot needs on top: one Parquet file per
table and UTC hour, flush files merged into it at exit, a manifest that records
the run (ADR 0005 item 1) and the deliveries per hour and broker (ing_03), and
a refusal to write the manifest while a flush file is unmerged or a delivery
key repeats (ing_01).
"""

from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from pipeline.common import snapshot as common
from pipeline.common.snapshot import SnapshotError

FLUSH_MARKER = ".flush-"
DELIVERY_TABLE = "mqtt_message"
# snp_01's fields, then a data content hash per table for reproduction. ADR
# 0005 item 1: the run parameters and the stop reason ("reduction_complete" for
# the reduction, which has no END_AT or MAX_RAW_BYTES and records them as
# null). ing_03: deliveries received, stored, counted and merged per hour and
# broker.
REQUIRED_METADATA = (
    *common.REQUIRED_METADATA,
    "brokers",
    "topics",
    "end_at",
    "max_raw_bytes",
    "stop_reason",
    "content_hashes",
    "deliveries",
    "decoder_version",
    "schema_version",
)


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


def _check_capture_files(partial: Path, files: list[Path]) -> dict[str, object]:
    unmerged = [path.name for path in files if FLUSH_MARKER in path.name]
    if unmerged:
        raise SnapshotError(f"{len(unmerged)} flush files are not merged")
    return {"delivery_rows": check_unique_delivery_keys(partial)}


def write_manifest(partial: Path, metadata: Mapping[str, object]) -> Path:
    """Write the manifest of a capture or reduction snapshot.

    Args:
        partial: The run's `<id>.partial/` folder, README already written.
        metadata: At least the REQUIRED_METADATA fields; written as given.

    Returns:
        The manifest path.

    Raises:
        SnapshotError: if a field is missing, the id does not match the
            folder, a flush file has not been merged or a delivery key repeats.
    """
    return common.write_manifest(
        partial, metadata, required=REQUIRED_METADATA, check=_check_capture_files
    )
