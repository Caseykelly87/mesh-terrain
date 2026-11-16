"""Content hashes of artifacts: what two identical runs must share (snp_01).

Parquet and COG writers embed library versions and layout choices in their
files, so `docs/INTERFACE.md` hashes content instead:

- A table (Parquet, GeoParquet): sha256 over, for each column in order, its
  name, its Arrow type, its validity bitmap and its non-null values, as
  little-endian bytes for numbers and booleans and as Python's repr of each
  value for text and binary (WKB geometry included). Row order counts.
- A raster: sha256 over its band count, data type, shape, transform, CRS
  (EPSG code) and nodata, then every band's cells in row order as
  little-endian bytes, read in windows of rows so no raster is read whole.
- Text (Markdown): the file's sha256, since nothing in it varies.

The manifest is not hashed this way; it holds the time of the run.
"""

import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import rasterio
from rasterio.windows import Window

from pipeline.common.snapshot import sha256_file

ROWS_PER_READ = 256


def table_content_sha256(path: Path) -> str:
    """Content hash of a Parquet file's rows.

    Args:
        path: The Parquet file.

    Returns:
        Hex sha256.
    """
    table = pq.read_table(path)
    digest = hashlib.sha256()
    for name, column in zip(table.column_names, table.columns, strict=True):
        digest.update(f"{name}:{column.type}\n".encode())
        _update_column(digest, column.combine_chunks())
    return digest.hexdigest()


def _update_column(digest: Any, column: Any) -> None:
    valid = np.asarray(column.is_valid().to_numpy(zero_copy_only=False), dtype=np.bool_)
    digest.update(np.packbits(valid).tobytes())
    values = column.drop_null()
    kind = column.type
    if pa.types.is_integer(kind) or pa.types.is_floating(kind) or pa.types.is_boolean(kind):
        array = np.asarray(values.to_numpy(zero_copy_only=False))
        digest.update(array.astype(array.dtype.newbyteorder("<")).tobytes())
    else:
        digest.update(repr(values.to_pylist()).encode())


def raster_content_sha256(path: Path) -> str:
    """Content hash of a raster's cells and georeferencing.

    Args:
        path: The GeoTIFF or COG.

    Returns:
        Hex sha256.
    """
    digest = hashlib.sha256()
    with rasterio.open(path) as raster:
        header = (
            raster.count,
            raster.dtypes,
            raster.shape,
            tuple(raster.transform)[:6],
            raster.crs.to_epsg() if raster.crs else None,
            raster.nodata,
        )
        digest.update(repr(header).encode())
        for band in range(1, raster.count + 1):
            for row in range(0, raster.height, ROWS_PER_READ):
                window = Window(0, row, raster.width, min(ROWS_PER_READ, raster.height - row))
                cells = raster.read(band, window=window)
                digest.update(cells.astype(cells.dtype.newbyteorder("<")).tobytes())
    return digest.hexdigest()


def text_content_sha256(path: Path) -> str:
    """Content hash of a text artifact: its file sha256."""
    return sha256_file(path)


def content_sha256(path: Path) -> str:
    """Content hash by file type: Parquet rows, raster cells, or the file itself."""
    if path.suffix == ".parquet":
        return table_content_sha256(path)
    if path.suffix == ".tif":
        return raster_content_sha256(path)
    return text_content_sha256(path)
