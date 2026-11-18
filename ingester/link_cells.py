"""Position cells and boxes under `lnk_02`, `lnk_05` and `pos_01`, in whole numbers where possible.

A position's cell is the triple the firmware sent: `latitude_i`, `longitude_i`
(1e-7 degrees, EPSG:4326) and `precision_bits` (`pos_01`). On each axis the
cell is the half-open interval low <= value < low + width, width being
2^(32 - precision_bits) and low the stored value minus half the width for 1 to
31 bits (the firmware sends the cell centre) or the stored value itself at 32
bits (the firmware sends the raw value). Every value lies in exactly one cell
at each precision (Casey, issue #44, 2026-11-17, item 3). Worked example from
ADR 0003: a stored -6 at 30 bits has width 4 and low -8, so its cell is
[-8, -4).

Two cells, or two boxes, overlap when on both axes the larger low is less than
the smaller high; cells that only touch do not. Two positions are the same
place when their cells overlap or their stored coordinates lie within
`STATIONARY_FLOOR_M` of each other by the WGS84 geodesic, inclusive; the test
is pairwise and never chained.

A node's box over a set of positions it is stationary at covers the cells of
those positions that hold no finer one of them (a cell holds a position when
the position's stored value lies in it on both axes, and finer means more
precision bits). One such cell is the box; two or more give the smallest
rectangle around them, `merged`, which only the floor allows (ADR 0003,
Link-rule amendment, D3'). Half-widths follow `pos_01`'s WGS84 formula: at the
stored latitude for a single cell, and from (north_i - south_i) / 2 and
(east_i - west_i) / 2 at the rectangle midpoint's latitude for a merged box
(PLAN, Data model). A box's centre is the midpoint of its edges, and the
distance between two boxes is the WGS84 geodesic between their centres
(`lnk_05`, Casey, issue #55, Q4).
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations
from typing import NamedTuple

from pyproj import Geod

MAX_BITS = 32
# Casey, issue #55, D4: 40 m, inclusive (ADR 0003, Link-rule amendment, the floor).
STATIONARY_FLOOR_M = 40.0
UNIT_DEG = 1e-7
# pos_01's WGS84 ellipsoid.
WGS84_A_M = 6_378_137.0
WGS84_F = 1 / 298.257223563
WGS84_E2 = WGS84_F * (2 - WGS84_F)
_GEOD = Geod(ellps="WGS84")

Edges = tuple[int, int, int, int]
"""south_i, north_i, west_i, east_i: half-open edges in 1e-7 degrees."""


class Cell(NamedTuple):
    """A position's cell as sent (`pos_01`): 1e-7 degree integers (EPSG:4326) and the bits kept."""

    latitude_i: int
    longitude_i: int
    precision_bits: int

    @property
    def edges(self) -> Edges:
        """The cell's half-open edges.

        Raises:
            ValueError: if precision_bits is outside 1 to 32.
        """
        if not 1 <= self.precision_bits <= MAX_BITS:
            raise ValueError(f"precision_bits {self.precision_bits} is outside 1 to {MAX_BITS}")
        width = 1 << (MAX_BITS - self.precision_bits)
        offset = 0 if self.precision_bits == MAX_BITS else width // 2
        south, west = self.latitude_i - offset, self.longitude_i - offset
        return south, south + width, west, west + width

    def holds(self, other: "Cell") -> bool:
        """Whether other's stored value lies in this cell on both axes."""
        south, north, west, east = self.edges
        return south <= other.latitude_i < north and west <= other.longitude_i < east


@dataclass(frozen=True)
class LinkBox:
    """The box an end uses under `lnk_02`: one cell, or the rectangle around merged fixes.

    Attributes:
        south_i: Lower latitude edge, 1e-7 degrees, included.
        north_i: Upper latitude edge, 1e-7 degrees, excluded.
        west_i: Lower longitude edge, 1e-7 degrees, included.
        east_i: Upper longitude edge, 1e-7 degrees, excluded.
        merged: True when the box covers two or more cells.
        precision_bits: The most among the cells the box covers.
        half_width_ns_m: North-south half-width, metres (`pos_01`).
        half_width_ew_m: East-west half-width, metres (`pos_01`).
    """

    south_i: int
    north_i: int
    west_i: int
    east_i: int
    merged: bool
    precision_bits: int
    half_width_ns_m: float
    half_width_ew_m: float

    @property
    def edges(self) -> Edges:
        return self.south_i, self.north_i, self.west_i, self.east_i

    @property
    def centre_deg(self) -> tuple[float, float]:
        """Latitude and longitude of the midpoint of the edges, degrees (EPSG:4326)."""
        latitude = (self.south_i * UNIT_DEG + self.north_i * UNIT_DEG) / 2
        return latitude, (self.west_i * UNIT_DEG + self.east_i * UNIT_DEG) / 2

    @property
    def larger_half_width_m(self) -> float:
        """The larger of the two half-widths, metres, as `position_uncertainty_m` sums it."""
        return max(self.half_width_ns_m, self.half_width_ew_m)

    @property
    def smaller_half_width_m(self) -> float:
        """The smaller of the two half-widths, metres: the square inscribed in the box."""
        return min(self.half_width_ns_m, self.half_width_ew_m)


def half_widths_m(height_i: int, width_i: int, latitude_deg: float) -> tuple[float, float]:
    """North-south and east-west half-widths of a box, metres, by `pos_01`'s WGS84 formula.

    Args:
        height_i: The box's extent in latitude, 1e-7 degrees.
        width_i: The box's extent in longitude, 1e-7 degrees.
        latitude_deg: The latitude the formula is taken at, degrees (EPSG:4326).
    """
    phi = math.radians(latitude_deg)
    sin2 = math.sin(phi) ** 2
    meridional = WGS84_A_M * (1 - WGS84_E2) / (1 - WGS84_E2 * sin2) ** 1.5
    normal = WGS84_A_M / math.sqrt(1 - WGS84_E2 * sin2)
    radians_per_unit = 0.5 * UNIT_DEG * math.pi / 180
    return (
        height_i * radians_per_unit * meridional,
        width_i * radians_per_unit * normal * math.cos(phi),
    )


@lru_cache(maxsize=65_536)
def cell_of(latitude_i: int, longitude_i: int, precision_bits: int) -> LinkBox:
    """The box of one cell, with `pos_01`'s half-widths at the stored latitude.

    Args:
        latitude_i: Stored latitude, 1e-7 degrees (EPSG:4326).
        longitude_i: Stored longitude, 1e-7 degrees (EPSG:4326).
        precision_bits: 1 to 32.

    Raises:
        ValueError: if precision_bits is outside 1 to 32.
    """
    south, north, west, east = Cell(latitude_i, longitude_i, precision_bits).edges
    ns_m, ew_m = half_widths_m(north - south, east - west, latitude_i * UNIT_DEG)
    return LinkBox(south, north, west, east, False, precision_bits, ns_m, ew_m)


def edges_overlap(a: Edges, b: Edges) -> bool:
    """Whether two half-open boxes share ground on both axes; touching is not overlap."""
    latitude = max(a[0], b[0]) < min(a[1], b[1])
    return latitude and max(a[2], b[2]) < min(a[3], b[3])


def cells_overlap(a: LinkBox, b: LinkBox) -> bool:
    """Whether two boxes overlap by `lnk_02`'s integer test, reading their edges."""
    return edges_overlap(a.edges, b.edges)


def geodesic_m(a_deg: tuple[float, float], b_deg: tuple[float, float]) -> float:
    """The WGS84 geodesic between two (latitude, longitude) points in degrees, metres."""
    _, _, distance_m = _GEOD.inv(a_deg[1], a_deg[0], b_deg[1], b_deg[0])
    return float(distance_m)


def box_distance_m(a: LinkBox, b: LinkBox) -> float:
    """The `lnk_05` distance: the WGS84 geodesic between two box centres, metres."""
    return geodesic_m(a.centre_deg, b.centre_deg)


@lru_cache(maxsize=262_144)
def same_place(a: Cell, b: Cell) -> bool:
    """Whether two positions are the same place: cells overlap, or within the floor (`lnk_02`)."""
    if edges_overlap(a.edges, b.edges):
        return True
    a_deg = (a.latitude_i * UNIT_DEG, a.longitude_i * UNIT_DEG)
    b_deg = (b.latitude_i * UNIT_DEG, b.longitude_i * UNIT_DEG)
    return geodesic_m(a_deg, b_deg) <= STATIONARY_FLOOR_M


@lru_cache(maxsize=65_536)
def all_same_place(cells: frozenset[Cell]) -> bool:
    """Whether every two of the cells are the same place, pairwise and never chained."""
    return all(same_place(a, b) for a, b in combinations(sorted(cells), 2))


def covered_cells(cells: Iterable[Cell]) -> frozenset[Cell]:
    """The cells holding no finer one of the others: a coarse cell around a fine one adds none."""
    distinct = set(cells)
    return frozenset(
        cell
        for cell in distinct
        if not any(
            other.precision_bits > cell.precision_bits and cell.holds(other) for other in distinct
        )
    )


@lru_cache(maxsize=65_536)
def box_around(cells: frozenset[Cell]) -> tuple[LinkBox, frozenset[Cell]]:
    """The box over a stationary node's positions, and the cells it covers.

    Args:
        cells: The cells of the positions the box is chosen from, at least one.

    Returns:
        The single covered cell's box, or the smallest rectangle around the
        covered cells (`merged`), with the covered cells.

    Raises:
        ValueError: if cells is empty.
    """
    if not cells:
        raise ValueError("a box needs at least one cell")
    covered = covered_cells(cells)
    if len(covered) == 1:
        (only,) = covered
        return cell_of(*only), covered
    edges = [cell.edges for cell in covered]
    south, north = min(e[0] for e in edges), max(e[1] for e in edges)
    west, east = min(e[2] for e in edges), max(e[3] for e in edges)
    midpoint_deg = (south * UNIT_DEG + north * UNIT_DEG) / 2
    ns_m, ew_m = half_widths_m(north - south, east - west, midpoint_deg)
    bits = max(cell.precision_bits for cell in covered)
    return LinkBox(south, north, west, east, True, bits, ns_m, ew_m), covered
