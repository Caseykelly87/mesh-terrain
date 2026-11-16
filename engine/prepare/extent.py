"""Which endpoints the engine would refuse a run for: each box plus 50 m against the DEM.

The engine builds one grid over every site and receiver box plus 50 m,
rounded out to multiples of 10 m (`engine.grid.run_grid`), and refuses the
whole run when that grid reaches beyond the DEM tiles' bounding box
(`engine.terrain_build._check_covers`). This module calls those two
functions unchanged on one endpoint at a time. The run grid is the bounding
box of the endpoints' own grids, since rounding out commutes with taking the
outermost edge, so a set of endpoints that each pass is a run the engine
cannot refuse for extent. It reads tile bounds only, no cells.
"""

from collections.abc import Sequence

from engine.errors import ConfigError
from engine.grid import run_grid
from engine.points import Endpoint
from engine.snapshots import NamedSnapshot
from engine.terrain_build import _check_covers, dem_resolution_m


def outside_extent(endpoints: Sequence[Endpoint], dem: NamedSnapshot) -> list[str]:
    """The ids of the endpoints whose box plus 50 m the engine would find outside the DEM.

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
    failing = []
    for endpoint in endpoints:
        try:
            _check_covers(dem, run_grid([endpoint], resolution_m))
        except ConfigError:
            # The message holds the endpoint's extent in metres, a real
            # position for a real node, so only the id leaves this function.
            failing.append(endpoint.endpoint_id)
    return failing
