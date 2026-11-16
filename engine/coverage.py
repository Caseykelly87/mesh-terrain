"""Coverage files: one COG per site and model, derived from `predictions.parquet` only.

Each file holds the predicted loss from one site, in dB, at the centre of
every 10 m cell of the run grid, interpolated linearly between the receivers
on a Delaunay triangulation of their box centres (`scipy.interpolate.
LinearNDInterpolator`). A cell outside the receivers' convex hull has no
value, as does every cell when fewer than three receivers with a value lie
off one line: the engine does not extrapolate. The values are read back from
the written `predictions.parquet`, so a coverage file can never disagree with
it (`docs/INTERFACE.md`). Sites are not combined; a caller wanting best-server
coverage takes the minimum across files.
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
import structlog
from scipy.interpolate import LinearNDInterpolator
from scipy.spatial import QhullError

from engine.grid import COVERAGE_RESOLUTION_M, RunGrid
from engine.rasters import FloatArray, write_array_cog
from terrain.path_loss import MODELS, Model

log = structlog.get_logger(component="engine.coverage")

MIN_RECEIVERS = 3


def coverage_values(xy_m: FloatArray, values_db: FloatArray, grid: RunGrid) -> FloatArray:
    """Interpolate receiver values to the centres of a grid's cells.

    Args:
        xy_m: Receiver points, shape (n, 2), EPSG:26915 metres.
        values_db: One loss per receiver, dB.
        grid: The coverage grid.

    Returns:
        Cells (rows, columns), north row first; NaN outside the receivers'
        hull, and everywhere when the receivers cannot be triangulated.
    """
    cells = np.full(grid.shape, np.nan)
    if len(values_db) < MIN_RECEIVERS:
        return cells
    try:
        interpolate = LinearNDInterpolator(xy_m, values_db, fill_value=np.nan)
    except QhullError:
        return cells
    columns = grid.west_m + (np.arange(grid.width) + 0.5) * grid.resolution_m
    rows = grid.north_m - (np.arange(grid.height) + 0.5) * grid.resolution_m
    x_m, y_m = np.meshgrid(columns, rows)
    interpolated: FloatArray = interpolate(x_m, y_m)
    return interpolated


def coverage_name(site_id: str, model: Model) -> str:
    """The artifact name of one site's coverage by one model."""
    return f"coverage_{site_id}_{model}.tif"


def write_coverage(folder: Path, models: tuple[Model, ...], grid: RunGrid) -> list[Path]:
    """Write a coverage COG per site and model from the run's `predictions.parquet`.

    Args:
        folder: The run's `.partial` folder, holding `predictions.parquet`.
        models: The run's models.
        grid: The run grid; coverage uses its extent at 10 m.

    Returns:
        The files written, sites in id order.
    """
    predictions = gpd.read_parquet(folder / "predictions.parquet")
    coverage_grid = grid.at_resolution(COVERAGE_RESOLUTION_M)
    written = []
    for site_id in sorted(predictions["site_id"].unique()):
        rows = predictions[predictions["site_id"] == site_id]
        ends = np.array([line.coords[-1] for line in rows.geometry])
        for model in (m for m in MODELS if m in models):
            values = rows[f"{model}_db_median"].to_numpy(dtype=np.float64, na_value=np.nan)
            known = ~np.isnan(values)
            cells = coverage_values(ends[known], values[known], coverage_grid)
            path = folder / coverage_name(str(site_id), model)
            write_array_cog(path, coverage_grid, cells)
            written.append(path)
    log.info(
        "coverage_written", files=len(written), cells=coverage_grid.width * coverage_grid.height
    )
    return written
