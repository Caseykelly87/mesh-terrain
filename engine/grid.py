"""The run's grid: the rectangle every terrain layer and coverage file is written on.

The extent is the bounding box of every site and receiver box plus a margin,
in EPSG:26915 metres (an `lnk_02` edge box by its four converted corners),
widened to eastings and northings that are multiples of 10 m. Those edges are
the edges of P.1812's 10 m cells (`terrain.cells`), so the terrain build's 1 m
layers sit on whole metres as `FineLayers` requires, and a coverage file at
10 m has one cell per P.1812 cell. The margin keeps
every profile's bilinear reads and every cell window inside the build.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

from affine import Affine

from engine.points import Endpoint

ALIGN_M = 10.0
"""Grid edges lie on multiples of this, the P.1812 cell size, metres."""

EXTENT_MARGIN_M = 50.0
"""Metres added beyond the outermost position box on every side."""

COVERAGE_RESOLUTION_M = 10.0
"""Cell size of the coverage files, metres: one cell per P.1812 cell."""


@dataclass(frozen=True, kw_only=True)
class RunGrid:
    """A north-up grid in EPSG:26915.

    Attributes:
        west_m: Western edge, easting in metres.
        south_m: Southern edge, northing in metres.
        east_m: Eastern edge, easting in metres.
        north_m: Northern edge, northing in metres.
        resolution_m: Pixel size in metres; divides the extent exactly.
    """

    west_m: float
    south_m: float
    east_m: float
    north_m: float
    resolution_m: float

    @property
    def width(self) -> int:
        """Columns."""
        return round((self.east_m - self.west_m) / self.resolution_m)

    @property
    def height(self) -> int:
        """Rows."""
        return round((self.north_m - self.south_m) / self.resolution_m)

    @property
    def shape(self) -> tuple[int, int]:
        """(rows, columns)."""
        return self.height, self.width

    @property
    def transform(self) -> Affine:
        """The affine transform from (column, row) to EPSG:26915."""
        return Affine(self.resolution_m, 0.0, self.west_m, 0.0, -self.resolution_m, self.north_m)

    def at_resolution(self, resolution_m: float) -> "RunGrid":
        """The same extent at another pixel size."""
        return replace(self, resolution_m=resolution_m)


def run_grid(
    endpoints: Sequence[Endpoint], resolution_m: float, margin_m: float = EXTENT_MARGIN_M
) -> RunGrid:
    """The grid covering every endpoint box plus the margin, edges on 10 m multiples.

    Args:
        endpoints: Every site and receiver of the run, EPSG:26915.
        resolution_m: Pixel size of the DEM in metres; must divide 10 m.
        margin_m: Metres added on every side.

    Returns:
        The run grid.

    Raises:
        ValueError: if there are no endpoints or the resolution does not
            divide 10 m.
    """
    if not endpoints:
        raise ValueError("a run grid needs at least one endpoint")
    if not (ALIGN_M / resolution_m).is_integer():
        raise ValueError(f"a {resolution_m} m DEM does not divide the 10 m cell grid")
    extents = [e.box.extent_m for e in endpoints]
    return RunGrid(
        west_m=_down(min(extent[0] for extent in extents) - margin_m),
        south_m=_down(min(extent[1] for extent in extents) - margin_m),
        east_m=_up(max(extent[2] for extent in extents) + margin_m),
        north_m=_up(max(extent[3] for extent in extents) + margin_m),
        resolution_m=resolution_m,
    )


def _down(value_m: float) -> float:
    return math.floor(value_m / ALIGN_M) * ALIGN_M


def _up(value_m: float) -> float:
    return math.ceil(value_m / ALIGN_M) * ALIGN_M
