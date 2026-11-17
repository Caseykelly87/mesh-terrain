"""The positions marker: Parquet file metadata saying whether a point layer holds real positions.

A layer carries the key `mesh_terrain:positions` in its file's key-value
metadata, `real` or `synthetic` (Casey, issue #44, item 4 (a)). It is
`synthetic` only when every row is made up; one real row makes it `real`.
Capture-derived layers are stamped `real` and the committed fixtures
`synthetic`; a prepared receiver layer that adds the made-up grid to one real
receiver is `real`. The engine does not read the marker yet: the
export rule queued after the x72y429 run records it in the run manifest, and
the export asset refuses a run that lacks it. Stamping rewrites the file
with the same rows and GeoParquet metadata.
"""

from pathlib import Path

import pyarrow.parquet as pq

POSITIONS_KEY = "mesh_terrain:positions"
REAL = "real"
SYNTHETIC = "synthetic"


def stamp_positions(path: Path, value: str) -> None:
    """Write the marker into a Parquet file's metadata, keeping its rows and other metadata.

    Args:
        path: The Parquet or GeoParquet file, rewritten in place.
        value: `real` or `synthetic`.

    Raises:
        ValueError: for any other value.
    """
    if value not in (REAL, SYNTHETIC):
        raise ValueError(f"the positions marker is real or synthetic, got {value!r}")
    table = pq.read_table(path)
    metadata = {**(table.schema.metadata or {}), POSITIONS_KEY.encode(): value.encode()}
    pq.write_table(table.replace_schema_metadata(metadata), path)


def positions_marker(path: Path) -> str | None:
    """The marker as written in the file, or None for a file without one."""
    metadata = pq.read_metadata(path).metadata or {}
    raw = metadata.get(POSITIONS_KEY.encode())
    return None if raw is None else bytes(raw).decode()
