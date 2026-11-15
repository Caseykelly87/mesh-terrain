"""Ground profiles sampled from a DEM along the straight line between two endpoints.

A profile is the bare-earth height at evenly spaced points from the transmitter to
the receiver, read from a north-up raster in EPSG:26915 (UTM zone 15N, metres).
Under 60 km the straight line in UTM is within a metre of the geodesic. The
heights are uncurved ground: the k = 4/3 bulge is added later by line of sight,
Fresnel and knife edge (`terrain.curvature`), and ITM and P.1812 apply their own
(ADR 0006 decision 3).

The caller opens the raster once and passes it in; this module reads from it and
opens nothing. Every read is a rasterio window around a stretch of the path plus
a margin, never the whole raster, and long paths are read in several windows so
that none grows past `max_window_px` on a side (a deviation from the plan's one
window per path, recorded in docs/STATUS.md). Values between pixel centres are bilinear
(`scipy.ndimage.map_coordinates` with order=1). This module does not decide what
a missing height means for a draw: nodata becomes NaN and the caller refuses it.
"""

import math
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
import structlog
from rasterio.io import DatasetReader
from rasterio.windows import Window
from scipy.ndimage import map_coordinates

from terrain.errors import OutOfExtentError

FloatArray = npt.NDArray[np.float64]
XY = tuple[float, float]

PROFILE_CRS_EPSG = 26915
_EXTENT_TOLERANCE_PX = 1e-9
_STEP_TOLERANCE = 1e-9

log = structlog.get_logger(__name__).bind(component="terrain.profile")


@dataclass(frozen=True, kw_only=True)
class PathProfile:
    """Ground, and later clutter, along one straight path in EPSG:26915.

    Attributes:
        distance_m: Distance from the transmitter in metres, starting at 0,
            ascending, uniform step.
        ground_m: Ground height in metres above NAVD88 at each distance, with no
            earth curvature applied. NaN where the DEM has no value.
        tx_xy_m: Transmitter easting and northing, metres.
        rx_xy_m: Receiver easting and northing, metres.
        clutter_m: Clutter height above ground in metres, or None where only the
            metro DEM exists. Filled by the clutter rule (unit U3b), not here.
    """

    distance_m: FloatArray
    ground_m: FloatArray
    tx_xy_m: XY
    rx_xy_m: XY
    clutter_m: FloatArray | None = None

    def __post_init__(self) -> None:
        """Check the arrays describe one path.

        Raises:
            ValueError: if the arrays differ in shape, hold fewer than two samples,
                or the distances do not start at 0 and ascend.
        """
        shape = self.distance_m.shape
        if self.distance_m.ndim != 1 or self.ground_m.shape != shape:
            raise ValueError("distance_m and ground_m must be 1-D arrays of one length")
        if self.clutter_m is not None and self.clutter_m.shape != shape:
            raise ValueError("clutter_m must match distance_m in length")
        if shape[0] < 2:
            raise ValueError("a profile needs at least two samples")
        if self.distance_m[0] != 0.0 or np.any(np.diff(self.distance_m) <= 0.0):
            raise ValueError("distance_m must start at 0 and ascend")

    @property
    def length_m(self) -> float:
        """Path length in metres."""
        return float(self.distance_m[-1])

    @property
    def step_m(self) -> float:
        """Spacing between samples in metres."""
        return float(self.distance_m[1] - self.distance_m[0])


def profile_distances_m(length_m: float, step_m: float) -> FloatArray:
    """Sample distances from 0 to the path length at a uniform step.

    The number of intervals is the length divided by the step, rounded up, so
    a path of 1000 m at 10 m has 101 samples. When the step does not divide the
    length, the step shrinks slightly so the last sample falls on the endpoint
    and the spacing stays uniform, which ITM's profile format requires.

    Args:
        length_m: Path length in metres.
        step_m: Largest allowed spacing in metres: 1 on the 1 m layers, 10 on the
            metro DEM.

    Returns:
        Distances in metres.

    Raises:
        ValueError: for a zero-length path or a step that is not positive.
    """
    if not math.isfinite(step_m) or step_m <= 0.0:
        raise ValueError(f"step_m must be positive, got {step_m}")
    if length_m <= 0.0:
        raise ValueError("zero-length path: the two endpoints coincide")
    intervals = max(1, math.ceil(length_m / step_m - _STEP_TOLERANCE))
    return np.linspace(0.0, length_m, intervals + 1)


def sample_profile(
    source: DatasetReader,
    tx_xy_m: XY,
    rx_xy_m: XY,
    step_m: float,
    *,
    max_window_px: int = 1000,
    margin_px: int = 2,
) -> PathProfile:
    """Sample uncurved ground heights along the line from tx to rx.

    Args:
        source: Open single-band DEM, north-up, in EPSG:26915 metres.
        tx_xy_m: Transmitter easting and northing in EPSG:26915, metres.
        rx_xy_m: Receiver easting and northing in EPSG:26915, metres.
        step_m: Largest sample spacing in metres (see `profile_distances_m`).
        max_window_px: Largest window side in pixels per read; 1 km on 1 m layers.
        margin_px: Pixels read beyond the samples on every side of a window.

    Returns:
        The profile, with nodata pixels turned into NaN before interpolation, so
        any sample whose four neighbouring pixels include one is NaN.

    Raises:
        ValueError: zero-length path, step not positive, or raster not north-up
            in EPSG:26915.
        OutOfExtentError: if any sample lies outside the raster.
    """
    length_m = math.hypot(rx_xy_m[0] - tx_xy_m[0], rx_xy_m[1] - tx_xy_m[1])
    distance_m = profile_distances_m(length_m, step_m)
    x_m, y_m = _points_along(tx_xy_m, rx_xy_m, distance_m / length_m)
    rows, cols = _checked_indices(source, x_m, y_m)
    ground_m = _logged_read(source, rows, cols, max_window_px, margin_px)
    return PathProfile(distance_m=distance_m, ground_m=ground_m, tx_xy_m=tx_xy_m, rx_xy_m=rx_xy_m)


def _logged_read(
    source: DatasetReader, rows: FloatArray, cols: FloatArray, max_window_px: int, margin_px: int
) -> FloatArray:
    """Sample every point, with a log line at the start and end of the reads."""
    raster = Path(source.name).name
    log.debug("profile_read_started", raster=raster, samples=int(rows.size))
    started = time.perf_counter()
    ground_m, windows = _sample_in_segments(source, rows, cols, max_window_px, margin_px)
    log.debug(
        "profile_read_finished",
        raster=raster,
        windows=windows,
        missing=int(np.isnan(ground_m).sum()),
        duration_s=round(time.perf_counter() - started, 6),
    )
    return ground_m


def _checked_indices(
    source: DatasetReader, x_m: FloatArray, y_m: FloatArray
) -> tuple[FloatArray, FloatArray]:
    """Pixel indices of the samples, after checking the grid and the extent."""
    raster = Path(source.name).name
    _check_grid(source, raster)
    rows, cols = _pixel_indices(source, x_m, y_m)
    _check_extent(rows, cols, source.height, source.width, raster)
    return rows, cols


def _points_along(tx_xy_m: XY, rx_xy_m: XY, fraction: FloatArray) -> tuple[FloatArray, FloatArray]:
    """Eastings and northings at fractions 0 to 1 of the way from tx to rx."""
    x_m: FloatArray = tx_xy_m[0] + (rx_xy_m[0] - tx_xy_m[0]) * fraction
    y_m: FloatArray = tx_xy_m[1] + (rx_xy_m[1] - tx_xy_m[1]) * fraction
    return x_m, y_m


def _check_grid(source: DatasetReader, raster: str) -> None:
    epsg = source.crs.to_epsg() if source.crs is not None else None
    if epsg != PROFILE_CRS_EPSG:
        raise ValueError(f"{raster} is in EPSG:{epsg}, expected EPSG:26915")
    transform = source.transform
    if transform.b != 0.0 or transform.d != 0.0 or transform.e >= 0.0:
        raise ValueError(f"{raster} is not a north-up raster")


def _pixel_indices(
    source: DatasetReader, x_m: FloatArray, y_m: FloatArray
) -> tuple[FloatArray, FloatArray]:
    """Fractional row and column indices, where an integer is a pixel centre.

    The grid is north-up (`_check_grid`), so the affine transform reduces to an
    origin and a pixel size per axis.
    """
    transform = source.transform
    cols: FloatArray = (x_m - transform.c) / transform.a - 0.5
    rows: FloatArray = (y_m - transform.f) / transform.e - 0.5
    return rows, cols


def _check_extent(rows: FloatArray, cols: FloatArray, height: int, width: int, raster: str) -> None:
    # The outer half pixel lies inside the raster but beyond the last centre;
    # sampling there takes the edge pixel's value.
    low = -0.5 - _EXTENT_TOLERANCE_PX
    inside_rows = rows.min() >= low and rows.max() <= height - 0.5 + _EXTENT_TOLERANCE_PX
    inside_cols = cols.min() >= low and cols.max() <= width - 0.5 + _EXTENT_TOLERANCE_PX
    if not (inside_rows and inside_cols):
        raise OutOfExtentError(f"profile leaves the extent of {raster}")


def _sample_in_segments(
    source: DatasetReader, rows: FloatArray, cols: FloatArray, max_window_px: int, margin_px: int
) -> tuple[FloatArray, int]:
    """Sample every point, reading one bounded window per run of samples."""
    span_px = max_window_px - 2 * margin_px - 3
    if span_px < 1:
        raise ValueError("max_window_px is too small for the margin")
    advance_px = max(abs(float(rows[1] - rows[0])), abs(float(cols[1] - cols[0])))
    per_window = rows.size if advance_px == 0.0 else int(span_px // advance_px) + 1
    ground_m = np.empty(rows.size, dtype=np.float64)
    windows = 0
    for start in range(0, rows.size, per_window):
        stop = min(rows.size, start + per_window)
        ground_m[start:stop] = _sample_window(source, rows[start:stop], cols[start:stop], margin_px)
        windows += 1
    return ground_m, windows


def _sample_window(
    source: DatasetReader, rows: FloatArray, cols: FloatArray, margin_px: int
) -> FloatArray:
    """Read the window around these samples and interpolate them bilinearly."""
    row_start = max(0, math.floor(rows.min()) - margin_px)
    row_stop = min(source.height, math.floor(rows.max()) + 2 + margin_px)
    col_start = max(0, math.floor(cols.min()) - margin_px)
    col_stop = min(source.width, math.floor(cols.max()) + 2 + margin_px)
    window = Window(col_start, row_start, col_stop - col_start, row_stop - row_start)
    block = source.read(1, window=window, masked=True)
    heights = np.ma.filled(block.astype(np.float64), np.nan)
    # mode="nearest" only acts in the outer half pixel of the raster; elsewhere
    # the margin keeps every sample's four neighbours inside the window.
    values = map_coordinates(heights, [rows - row_start, cols - col_start], order=1, mode="nearest")
    return np.asarray(values, dtype=np.float64)
