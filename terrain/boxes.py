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

Draws never use a half-width below the GPS error floor (`floor_m`; Casey,
issue #55 comments 6962700456 and 7042577618, ADR 0009): a square's
half-width is raised to it, and an edge box is widened about its centre on
each axis below it, in latitude and longitude with `pos_01`'s WGS84 radii at
the box-centre latitude, an extension of the same exception Casey granted.
An exact point (half-width 0) is a model point, not a fix, and stays exact
(Casey, issue #55 comment 7043900875, item 1).
A floor of 0 changes nothing.

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

WGS84_A_M = 6_378_137.0
"""WGS84 semi-major axis, metres, as `pos_01` states it (`ingester/link_cells.py` holds
the same value for the warehouse side)."""

WGS84_F = 1 / 298.257223563
"""WGS84 flattening, as `pos_01` states it."""

WGS84_E2 = WGS84_F * (2 - WGS84_F)
"""WGS84 first eccentricity squared, e^2 = f(2 - f), as `pos_01` states it."""

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


def check_floor(floor_m: float) -> None:
    """Refuse a GPS error floor that is negative or not finite.

    Args:
        floor_m: The floor, metres.

    Raises:
        ValueError: if it is negative or not finite.
    """
    if not (math.isfinite(floor_m) and floor_m >= 0.0):
        raise ValueError(f"floor_m must be a finite number of at least 0, got {floor_m}")


def floor_units(latitude_deg: float, floor_m: float) -> tuple[float, float]:
    """A floor in edge units, north-south and east-west, by `pos_01`'s WGS84 radii.

    The inverse of `pos_01`'s half-width formula: a unit is 1e-7 x (pi / 180)
    x M metres north-south and 1e-7 x (pi / 180) x N x cos(phi) metres
    east-west, with M and N the meridional and normal radii at latitude phi.

    Args:
        latitude_deg: The latitude the radii are taken at, degrees (EPSG:4326).
        floor_m: The floor, metres.

    Returns:
        The floor in 1e-7 degree units on each axis, north-south first.
    """
    phi = math.radians(latitude_deg)
    sin_squared = math.sin(phi) ** 2
    meridional_m = WGS84_A_M * (1 - WGS84_E2) / (1 - WGS84_E2 * sin_squared) ** 1.5
    normal_m = WGS84_A_M / math.sqrt(1 - WGS84_E2 * sin_squared)
    radians_per_unit = UNIT_DEG * math.pi / 180
    return (
        floor_m / (radians_per_unit * meridional_m),
        floor_m / (radians_per_unit * normal_m * math.cos(phi)),
    )


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

    def drawn_half_width_m(self, floor_m: float) -> float:
        """The half-width the draws use: at least the floor, 0 for an exact point.

        Raises:
            ValueError: if the floor is negative or not finite.
        """
        check_floor(floor_m)
        return max(self.half_width_m, floor_m) if self.has_area else 0.0

    def extent_m(self, margin_m: float = 0.0, floor_m: float = 0.0) -> Extent:
        """The drawn box's west, south, east and north edges plus a margin, EPSG:26915 metres.

        The half-width and the margin are added first, as the run grid has
        always done, so a square gives the same grid edges to the last bit.

        Args:
            margin_m: Metres added on every side.
            floor_m: The GPS error floor the draws use, metres.
        """
        x_m, y_m = self.centre_xy_m
        reach_m = self.drawn_half_width_m(floor_m) + margin_m
        return x_m - reach_m, y_m - reach_m, x_m + reach_m, y_m + reach_m

    @property
    def reach_m(self) -> float:
        """Distance from the centre to the farthest corner, metres."""
        return self.half_width_m * math.sqrt(2.0)

    def drawn_reach_m(self, floor_m: float) -> float:
        """Distance from the centre to the farthest corner of the drawn box, metres.

        Args:
            floor_m: The GPS error floor the draws use, metres.
        """
        return self.drawn_half_width_m(floor_m) * math.sqrt(2.0)

    def place(self, units: UnitArray, floor_m: float) -> list[XY]:
        """Points in the box: each unit scales the drawn half-width on its axis.

        Args:
            units: Draw numbers, shape (points, 2).
            floor_m: The GPS error floor, metres.

        Returns:
            Easting and northing of each point, EPSG:26915 metres.

        Raises:
            ValueError: if the floor is negative or not finite.
        """
        half_m = self.drawn_half_width_m(floor_m)
        x_m = self.centre_xy_m[0] + half_m * units[:, 0]
        y_m = self.centre_xy_m[1] + half_m * units[:, 1]
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
                90 degrees of latitude or -180 to 180 of longitude. The
                message names the rule and never an edge value: it can reach
                a log beside a node's pseudonym.
        """
        edges = (self.south_i, self.north_i, self.west_i, self.east_i)
        if not all(isinstance(edge, int) and not isinstance(edge, bool) for edge in edges):
            raise ValueError("box edges must be whole numbers of 1e-7 degrees")
        if self.south_i >= self.north_i:
            raise ValueError("south_i must be below north_i")
        if self.west_i >= self.east_i:
            raise ValueError("west_i must be below east_i")
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
        return _corners(self.drawn_edges(0.0))

    def drawn_edges(self, floor_m: float) -> tuple[float, float, float, float]:
        """South, north, west and east edges the draws use, 1e-7 degrees.

        An axis whose half-width is below the floor, in metres by `pos_01`'s
        radii at the box-centre latitude, is widened about the centre to the
        floor; the other axis keeps its edges. With a floor of 0 these are the
        box's own edges.

        Args:
            floor_m: The GPS error floor, metres.

        Raises:
            ValueError: if the floor is negative or not finite.
        """
        check_floor(floor_m)
        south, north = float(self.south_i), float(self.north_i)
        west, east = float(self.west_i), float(self.east_i)
        if floor_m == 0.0:
            return south, north, west, east
        centre_ns_i, centre_ew_i = (south + north) / 2, (west + east) / 2
        floor_ns_i, floor_ew_i = floor_units(centre_ns_i * UNIT_DEG, floor_m)
        if (north - south) / 2 < floor_ns_i:
            south, north = centre_ns_i - floor_ns_i, centre_ns_i + floor_ns_i
        if (east - west) / 2 < floor_ew_i:
            west, east = centre_ew_i - floor_ew_i, centre_ew_i + floor_ew_i
        return south, north, west, east

    def extent_m(self, margin_m: float = 0.0, floor_m: float = 0.0) -> Extent:
        """West, south, east and north of the drawn box's corners plus a margin, EPSG:26915.

        The converted edges bow by millimetres (5 mm on a 16-bit cell), which
        the run grid's 50 m margin covers.

        Args:
            margin_m: Metres added on every side.
            floor_m: The GPS error floor the draws use, metres.
        """
        corners = self.corners_xy_m if floor_m == 0.0 else _corners(self.drawn_edges(floor_m))
        eastings_m = [corner[0] for corner in corners]
        northings_m = [corner[1] for corner in corners]
        return (
            min(eastings_m) - margin_m,
            min(northings_m) - margin_m,
            max(eastings_m) + margin_m,
            max(northings_m) + margin_m,
        )

    @property
    def reach_m(self) -> float:
        """Distance from the centre to the farthest corner, EPSG:26915 metres."""
        return max(math.dist(self.centre_xy_m, corner) for corner in self.corners_xy_m)

    def drawn_reach_m(self, floor_m: float) -> float:
        """Distance from the centre to the farthest corner of the drawn box, EPSG:26915 metres.

        Args:
            floor_m: The GPS error floor the draws use, metres.
        """
        if floor_m == 0.0:
            return self.reach_m
        corners = _corners(self.drawn_edges(floor_m))
        return max(math.dist(self.centre_xy_m, corner) for corner in corners)

    def place(self, units: UnitArray, floor_m: float) -> list[XY]:
        """Points between the drawn edges, uniform in latitude and longitude.

        Units of -1 to 1, the upper end excluded, map onto the half-open
        edges (`drawn_edges`): the first column onto longitude, the second
        onto latitude.

        Args:
            units: Draw numbers, shape (points, 2).
            floor_m: The GPS error floor, metres.

        Returns:
            Easting and northing of each point, EPSG:26915 metres.

        Raises:
            ValueError: if the floor is negative or not finite.
        """
        south, north, west, east = self.drawn_edges(floor_m)
        fraction = (units + 1.0) / 2.0
        longitude_deg = (west + fraction[:, 0] * (east - west)) * UNIT_DEG
        latitude_deg = (south + fraction[:, 1] * (north - south)) * UNIT_DEG
        x_m, y_m = GEOGRAPHIC_TO_UTM.transform(longitude_deg, latitude_deg)
        return [(float(x), float(y)) for x, y in zip(x_m, y_m, strict=True)]


def _corners(edges: tuple[float, float, float, float]) -> tuple[XY, XY, XY, XY]:
    """South-west, south-east, north-east and north-west corners of edges in 1e-7 degrees."""
    south, north, west, east = edges
    latitudes_deg = np.array([south, south, north, north]) * UNIT_DEG
    longitudes_deg = np.array([west, east, east, west]) * UNIT_DEG
    x_m, y_m = GEOGRAPHIC_TO_UTM.transform(longitudes_deg, latitudes_deg)
    south_west, south_east, north_east, north_west = (
        (float(x), float(y)) for x, y in zip(x_m, y_m, strict=True)
    )
    return south_west, south_east, north_east, north_west


PositionBox = EndpointBox | EdgeBox
"""Where an endpoint is known to lie: a grid square or an `lnk_02` edge box."""
