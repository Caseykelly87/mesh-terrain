"""Position boxes: where an endpoint is known to lie, and points drawn inside one.

A node's reported position is only known to a box. A box is either a square in
EPSG:26915 (UTM zone 15N, metres) with sides along the grid axes
(`EndpointBox`, for callers without cells and the committed fixtures), or the
`lnk_02` box itself, a rectangle in latitude and longitude given by its edges
in 1e-7 degrees (`EdgeBox`, ADR 0008). In EPSG:26915 an edge box is turned by
the meridian convergence, about 1.6 degrees at the St. Louis tiles, with true
north west of grid north. Points are drawn uniformly between its edges in
latitude and longitude and each one converted to EPSG:26915, so every point
lies inside the box and the turn comes from the conversion. That interpolation
between a box's own edges is the one piece of arithmetic on degrees, an
exception to computing in EPSG:26915 that Casey granted on PR #73.

This module draws no random numbers; `terrain.draws` does, and passes them in.
"""

import math
from dataclasses import dataclass
from functools import cached_property

import numpy as np
import numpy.typing as npt
from pyproj import Transformer

from terrain.profile import XY

UNIT_DEG = 1e-7
"""Degrees per unit of a box edge: edges are whole numbers of 1e-7 degrees."""

LATITUDE_LIMIT_I = 900_000_000
"""90 degrees in edge units."""

LONGITUDE_LIMIT_I = 1_800_000_000
"""180 degrees in edge units."""

GEOGRAPHIC_TO_UTM = Transformer.from_crs(4326, 26915, always_xy=True)
"""EPSG:4326 longitude and latitude to EPSG:26915 metres. PROJ names the operation
"Inverse of NAD83 to WGS 84 (1) + UTM zone 15N", a null datum shift; the
engine's point layers and `ingester.engine_inputs` use the same one."""

UnitArray = npt.NDArray[np.float64]
"""Draw numbers from -1 to 1, the upper end excluded, one row per point: the
first column moves the easting (or longitude), the second the northing (or
latitude)."""

Extent = tuple[float, float, float, float]
"""West, south, east and north edges in EPSG:26915 metres."""


@dataclass(frozen=True, kw_only=True)
class EndpointBox:
    """A square an endpoint is known to lie in, sides along the EPSG:26915 grid.

    Attributes:
        centre_xy_m: Box centre, easting and northing in EPSG:26915, metres.
        half_width_m: Half the side of the box in metres; 0 for a known point.
    """

    centre_xy_m: XY
    half_width_m: float

    def __post_init__(self) -> None:
        """Check the half-width.

        Raises:
            ValueError: if the half-width is negative or not finite.
        """
        if not (math.isfinite(self.half_width_m) and self.half_width_m >= 0.0):
            raise ValueError(f"half_width_m must be finite and at least 0, got {self.half_width_m}")

    @property
    def has_area(self) -> bool:
        """Whether a drawn point can differ from the centre."""
        return self.half_width_m > 0.0

    @property
    def extent_m(self) -> Extent:
        """The box's west, south, east and north edges, EPSG:26915 metres."""
        x_m, y_m = self.centre_xy_m
        half_m = self.half_width_m
        return x_m - half_m, y_m - half_m, x_m + half_m, y_m + half_m

    @property
    def reach_m(self) -> float:
        """Distance from the centre to the farthest corner, metres."""
        return self.half_width_m * math.sqrt(2.0)

    def place(self, units: UnitArray) -> list[XY]:
        """Points in the box: each unit scales the half-width on its axis.

        Args:
            units: Draw numbers, shape (points, 2).

        Returns:
            Easting and northing of each point, EPSG:26915 metres.
        """
        x_m = self.centre_xy_m[0] + self.half_width_m * units[:, 0]
        y_m = self.centre_xy_m[1] + self.half_width_m * units[:, 1]
        return [(float(x), float(y)) for x, y in zip(x_m, y_m, strict=True)]


@dataclass(frozen=True, kw_only=True)
class EdgeBox:
    """The `lnk_02` box: a rectangle in latitude and longitude (ADR 0008).

    The edges are whole numbers of 1e-7 degrees on WGS84 (EPSG:4326),
    half-open as `lnk_02` defines them: south and west included, north and
    east excluded. The box is one cell, or the rectangle around fixes the
    stationary floor merged.

    Attributes:
        south_i: Lower latitude edge, 1e-7 degrees.
        north_i: Upper latitude edge, 1e-7 degrees.
        west_i: Lower longitude edge, 1e-7 degrees.
        east_i: Upper longitude edge, 1e-7 degrees.
    """

    south_i: int
    north_i: int
    west_i: int
    east_i: int

    def __post_init__(self) -> None:
        """Check the edges.

        Raises:
            ValueError: if an edge is not a whole number, south is not below
                north or west not below east, or an edge lies outside -90 to
                90 degrees of latitude or -180 to 180 of longitude.
        """
        edges = (self.south_i, self.north_i, self.west_i, self.east_i)
        if not all(isinstance(edge, int) and not isinstance(edge, bool) for edge in edges):
            raise ValueError(f"box edges must be whole numbers of 1e-7 degrees, got {edges}")
        if self.south_i >= self.north_i:
            raise ValueError(f"south_i {self.south_i} must be below north_i {self.north_i}")
        if self.west_i >= self.east_i:
            raise ValueError(f"west_i {self.west_i} must be below east_i {self.east_i}")
        if self.south_i < -LATITUDE_LIMIT_I or self.north_i > LATITUDE_LIMIT_I:
            raise ValueError("the box's latitude lies outside -90 to 90 degrees")
        if self.west_i < -LONGITUDE_LIMIT_I or self.east_i > LONGITUDE_LIMIT_I:
            raise ValueError("the box's longitude lies outside -180 to 180 degrees")

    @property
    def has_area(self) -> bool:
        """Always true: an edge box is at least one unit on each axis."""
        return True

    @cached_property
    def centre_xy_m(self) -> XY:
        """The midpoint of the edges (`lnk_02`), converted to EPSG:26915 metres."""
        latitude_deg = (self.south_i + self.north_i) / 2 * UNIT_DEG
        longitude_deg = (self.west_i + self.east_i) / 2 * UNIT_DEG
        x_m, y_m = GEOGRAPHIC_TO_UTM.transform(longitude_deg, latitude_deg)
        return float(x_m), float(y_m)

    @cached_property
    def corners_xy_m(self) -> tuple[XY, XY, XY, XY]:
        """South-west, south-east, north-east and north-west corners, EPSG:26915 metres."""
        latitudes_i = np.array([self.south_i, self.south_i, self.north_i, self.north_i])
        longitudes_i = np.array([self.west_i, self.east_i, self.east_i, self.west_i])
        x_m, y_m = GEOGRAPHIC_TO_UTM.transform(longitudes_i * UNIT_DEG, latitudes_i * UNIT_DEG)
        south_west, south_east, north_east, north_west = (
            (float(x), float(y)) for x, y in zip(x_m, y_m, strict=True)
        )
        return south_west, south_east, north_east, north_west

    @property
    def extent_m(self) -> Extent:
        """West, south, east and north of the four corners, EPSG:26915 metres.

        The converted edges bow by millimetres (5 mm on a 16-bit cell), which
        the run grid's 50 m margin covers.
        """
        eastings_m = [corner[0] for corner in self.corners_xy_m]
        northings_m = [corner[1] for corner in self.corners_xy_m]
        return min(eastings_m), min(northings_m), max(eastings_m), max(northings_m)

    @property
    def reach_m(self) -> float:
        """Distance from the centre to the farthest corner, EPSG:26915 metres."""
        return max(math.dist(self.centre_xy_m, corner) for corner in self.corners_xy_m)

    def place(self, units: UnitArray) -> list[XY]:
        """Points between the edges, uniform in latitude and longitude.

        Units of -1 to 1, the upper end excluded, map onto the half-open
        edges: the first column onto longitude, the second onto latitude.

        Args:
            units: Draw numbers, shape (points, 2).

        Returns:
            Easting and northing of each point, EPSG:26915 metres.
        """
        fraction = (units + 1.0) / 2.0
        longitude_deg = (self.west_i + fraction[:, 0] * (self.east_i - self.west_i)) * UNIT_DEG
        latitude_deg = (self.south_i + fraction[:, 1] * (self.north_i - self.south_i)) * UNIT_DEG
        x_m, y_m = GEOGRAPHIC_TO_UTM.transform(longitude_deg, latitude_deg)
        return [(float(x), float(y)) for x, y in zip(x_m, y_m, strict=True)]


PositionBox = EndpointBox | EdgeBox
"""Where an endpoint is known to lie: a grid square or an `lnk_02` edge box."""
