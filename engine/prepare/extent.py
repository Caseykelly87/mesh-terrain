"""Which endpoints the engine would refuse a run for: each box plus 50 m against the DEM.

The engine builds one grid over every site and receiver box, widened to the
GPS error floor a prepared run writes (5 m, ADR 0009), plus 50 m,
rounded out to multiples of 10 m (`engine.grid.run_grid`), and refuses the
whole run when that grid is not inside the DEM tiles' bounding box
(`engine.terrain_build.check_covers`, through `dem_extent` and
`grid_inside`). This module applies the same three functions to one endpoint
at a time. The run grid is the bounding box of the endpoints' own grids,
since rounding out commutes with taking the outermost edge, so a set of
endpoints that each pass is a run the engine cannot refuse for extent. It
reads tile bounds only, no cells.
"""

from collections.abc import Sequence

from engine.grid import run_grid
from engine.points import Endpoint
from engine.prepare.run_config import POSITION_FLOOR_M
from engine.snapshots import NamedSnapshot
from engine.terrain_build import dem_extent, dem_resolution_m, grid_inside


def outside_extent(endpoints: Sequence[Endpoint], dem: NamedSnapshot) -> list[str]:
    """The ids of the endpoints whose box, widened to the floor, plus 50 m lies outside the DEM.

    Args:
        endpoints: Sites and receivers, EPSG:26915.
        dem: The verified DEM snapshot the run names.

    Returns:
        The failing endpoint ids, in input order; empty when all pass.

    Raises:
        ConfigError: when a DEM tile does not open or is not north-up in
            EPSG:26915, as the engine would refuse it.
    """
    resolution_m = dem_resolution_m(dem)
    extent = dem_extent(dem)
    return [
        endpoint.endpoint_id
        for endpoint in endpoints
        if not grid_inside(run_grid([endpoint], resolution_m, floor_m=POSITION_FLOOR_M), extent)
    ]
