"""Writing the run's Cloud Optimized GeoTIFFs, one window of cells at a time.

Every raster the engine writes is float32 in EPSG:26915 with nodata -9999,
built block by block into a tiled GeoTIFF in a temporary folder and copied to
its place by GDAL's COG driver (deflate, floating-point predictor, internal
overviews by nearest neighbour). No layer is held whole in memory, so the same
code builds the 1 km fixture and the 10 km test tile. NaN in a block becomes
nodata on disk; reading with `masked=True` turns it back.
"""

import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import numpy.typing as npt
import rasterio
import rasterio.shutil
from rasterio.windows import Window

from engine.grid import RunGrid

NODATA = -9999.0
BLOCK_PX = 512
COG_OPTIONS = {
    "COMPRESS": "DEFLATE",
    "PREDICTOR": "YES",
    "BLOCKSIZE": str(BLOCK_PX),
    "OVERVIEW_RESAMPLING": "NEAREST",
}

FloatArray = npt.NDArray[np.float64]
CellSource = Callable[[Window], npt.NDArray[np.floating]]
"""Returns the cells of one window of the run grid, NaN where there is no value."""


def windows(height: int, width: int, block_px: int = BLOCK_PX) -> Iterator[Window]:
    """Windows of at most `block_px` on a side covering a raster, row by row."""
    for row in range(0, height, block_px):
        for col in range(0, width, block_px):
            yield Window(col, row, min(block_px, width - col), min(block_px, height - row))


def write_cog(path: Path, grid: RunGrid, cells: CellSource) -> None:
    """Write one float32 COG on the grid, window by window.

    Args:
        path: The COG to write.
        grid: The grid, EPSG:26915.
        cells: Gives each window's cells, NaN for no value.
    """
    profile = {
        "driver": "GTiff",
        "height": grid.height,
        "width": grid.width,
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:26915",
        "transform": grid.transform,
        "nodata": NODATA,
        "tiled": True,
        "blockxsize": BLOCK_PX,
        "blockysize": BLOCK_PX,
    }
    with tempfile.TemporaryDirectory() as scratch:
        tiff = Path(scratch) / "layer.tif"
        with rasterio.open(tiff, "w", **profile) as dst:
            for window in windows(grid.height, grid.width):
                block = np.asarray(cells(window), dtype=np.float32)
                dst.write(np.where(np.isnan(block), np.float32(NODATA), block), 1, window=window)
        rasterio.shutil.copy(tiff, path, driver="COG", **COG_OPTIONS)


def write_array_cog(path: Path, grid: RunGrid, array: FloatArray) -> None:
    """Write a whole small array (a coverage grid) as a COG."""

    def cells(window: Window) -> FloatArray:
        rows, cols = window.toslices()
        block: FloatArray = array[rows, cols]
        return block

    write_cog(path, grid, cells)


def read_cells(raster: rasterio.io.DatasetReader, window: Window) -> FloatArray:
    """One window of band 1 as float64, NaN where the raster has no value."""
    block = raster.read(1, window=window, masked=True)
    filled: FloatArray = np.ma.filled(block.astype(np.float64), np.nan)
    return filled
