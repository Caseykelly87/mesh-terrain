"""A USGS 1 m DEM tile by name, and the made-up receiver grid over it.

USGS names its 10 km 1 m tiles xAyB in EPSG:26915: the tile covers eastings
A to A + 1 and northings B - 1 to B in 10 km units, so y names the northern
edge (`docs/PLAN.md`, Phase 3). The grid is a regular lattice of exact points
(box half-width 0) inset from the tile edges; it stands in for receivers so a
run gives coverage maps and many paths to inspect. It holds no real position.
"""

import re
from dataclasses import dataclass

from engine.points import Endpoint
from terrain.draws import EndpointBox

TILE_SIZE_M = 10_000.0
TILE_NAME = re.compile(r"x(\d+)y(\d+)")
GRID_SPACING_M = 500.0
"""Distance between neighbouring grid receivers, metres."""
GRID_INSET_M = 250.0
"""Distance from each tile edge to the outermost grid receivers, metres."""


@dataclass(frozen=True, kw_only=True)
class Tile:
    """A 10 km tile in EPSG:26915.

    Attributes:
        name: The USGS name, such as x72y429.
        west_m: Western edge, easting in metres.
        south_m: Southern edge, northing in metres.
        east_m: Eastern edge, easting in metres.
        north_m: Northern edge, northing in metres.
    """

    name: str
    west_m: float
    south_m: float
    east_m: float
    north_m: float


def parse_tile(name: str) -> Tile:
    """The tile a USGS 1 m tile name covers.

    Args:
        name: xAyB, A and B whole numbers of 10 km in EPSG:26915.

    Returns:
        The tile; x72y429 is 720000 to 730000 E and 4280000 to 4290000 N.

    Raises:
        ValueError: for a name that is not of the form xAyB.
    """
    match = TILE_NAME.fullmatch(name)
    if match is None:
        raise ValueError(f"{name!r} is not a USGS 1 m tile name such as x72y429")
    east_index, north_index = (int(group) for group in match.groups())
    return Tile(
        name=name,
        west_m=east_index * TILE_SIZE_M,
        south_m=(north_index - 1) * TILE_SIZE_M,
        east_m=(east_index + 1) * TILE_SIZE_M,
        north_m=north_index * TILE_SIZE_M,
    )


def grid_receivers(
    tile: Tile, spacing_m: float = GRID_SPACING_M, inset_m: float = GRID_INSET_M
) -> list[Endpoint]:
    """Exact receiver points every `spacing_m`, starting `inset_m` inside the tile edges.

    Ids are `grid-rRRcCC`: rows count from the north, as raster rows do, and
    columns from the west, both from 00. Points come row by row from the
    north-west corner.

    Args:
        tile: The tile, EPSG:26915.
        spacing_m: Distance between neighbouring points, metres.
        inset_m: Distance from each edge to the outermost points, metres.

    Returns:
        The grid receivers, box half-width 0.

    Raises:
        ValueError: when the spacing does not fit the inset tile a whole
            number of times.
    """
    steps = (TILE_SIZE_M - 2.0 * inset_m) / spacing_m
    if not steps.is_integer() or steps < 0:
        raise ValueError(f"a {spacing_m:g} m spacing does not fit {tile.name} inset {inset_m:g} m")
    count = int(steps) + 1
    return [
        Endpoint(
            endpoint_id=f"grid-r{row:02d}c{col:02d}",
            box=EndpointBox(
                centre_xy_m=(
                    tile.west_m + inset_m + col * spacing_m,
                    tile.north_m - inset_m - row * spacing_m,
                ),
                half_width_m=0.0,
            ),
        )
        for row in range(count)
        for col in range(count)
    ]
