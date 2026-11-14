"""Write landed tables as zstd Parquet and hash their data content (ADR 0005 item 6, snp_01).

The content hash is the hash of the rows, not of the file, since Parquet
writers embed metadata that can change between builds. Each row is rendered
as canonical JSON in allowlist column order (timestamps as ISO 8601 in UTC, floats
by repr) and hashed with sha256; the table's hash is the sum of the row
digests modulo 2**256, written as 64 hex characters. A sum does not depend on
row order or on how rows are split across hour files, so the same rows give
the same hash however a run flushed them.
"""

import hashlib
import json
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from ingester.allowlist import arrow_schema

_MODULUS = 2**256


def write_rows(path: Path, table: str, rows: Sequence[dict[str, Any]]) -> int:
    """Write rows as one zstd Parquet file with the table's allowlist schema.

    Args:
        path: The file to write; its folder is created.
        table: A landed table name from ingester.allowlist.TABLES.
        rows: Dicts holding exactly the table's columns.

    Returns:
        Bytes written.

    Raises:
        pyarrow.lib.ArrowInvalid: if a value does not fit its column type.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = pa.Table.from_pylist(list(rows), schema=arrow_schema(table))
    pq.write_table(data, path, compression="zstd")
    return path.stat().st_size


def read_rows(path: Path) -> list[dict[str, Any]]:
    """All rows of one Parquet file as dicts."""
    rows: list[dict[str, Any]] = pq.read_table(path).to_pylist()
    return rows


def _canonical(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def row_digest(row: dict[str, Any], columns: Sequence[str]) -> int:
    """sha256 of one row's canonical JSON, as an integer."""
    text = json.dumps([_canonical(row[name]) for name in columns], separators=(",", ":"))
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest(), "big")


def content_hash(paths: Iterable[Path], columns: Sequence[str]) -> str:
    """Data content hash of the rows in a set of Parquet files.

    Args:
        paths: The table's files.
        columns: The columns that make up the content, in a fixed order.
    """
    total = 0
    for path in paths:
        for row in read_rows(path):
            total = (total + row_digest(row, columns)) % _MODULUS
    return f"{total:064x}"
