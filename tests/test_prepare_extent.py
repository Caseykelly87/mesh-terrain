"""The extent check before a run: each endpoint's box plus 50 m inside the DEM.

The engine refuses a whole run when its extent, the bounding box of every
site and receiver box plus 50 m rounded out to 10 m, reaches outside the DEM
tiles (`engine.grid.run_grid`, `engine.terrain_build._check_covers`). The
check runs that same rule on each endpoint alone, so the endpoints that pass
make a run the engine cannot refuse for extent.

The synthetic DEM here is 1 m pixels covering 720000 to 721000 E and 4289000
to 4290000 N, EPSG:26915.
"""

from pathlib import Path

import numpy as np
import pytest
from prepare_snapshots import named, write_raster_snapshot

from engine.grid import run_grid
from engine.points import Endpoint
from engine.prepare.extent import outside_extent
from engine.snapshots import NamedSnapshot
from engine.terrain_build import check_sources, dem_resolution_m
from terrain.draws import EndpointBox

WEST_M, NORTH_M = 720_000.0, 4_290_000.0
MID_Y_M = 4_289_500.0


@pytest.fixture(scope="module")
def dem(tmp_path_factory: pytest.TempPathFactory) -> NamedSnapshot:
    root = tmp_path_factory.mktemp("snapshots")
    heights = np.full((1000, 1000), 150.0, dtype=np.float32)
    write_raster_snapshot(root, "dem-test", {"dem.tif": (heights, 1.0, (WEST_M, NORTH_M))})
    return named(root, "dem-test")["dem"]


def endpoint(name: str, x_m: float, y_m: float = MID_Y_M, half_width_m: float = 0.0) -> Endpoint:
    return Endpoint(
        endpoint_id=name, box=EndpointBox(centre_xy_m=(x_m, y_m), half_width_m=half_width_m)
    )


def test_a_point_well_inside_passes(dem: NamedSnapshot) -> None:
    assert outside_extent([endpoint("inside", 720_500.0)], dem) == []


def test_a_point_closer_than_50_m_to_the_edge_fails(dem: NamedSnapshot) -> None:
    assert outside_extent([endpoint("near-edge", 720_040.0)], dem) == ["near-edge"]


def test_the_box_counts_not_only_its_centre(dem: NamedSnapshot) -> None:
    passes = endpoint("box-440", 720_500.0, half_width_m=440.0)
    fails = endpoint("box-460", 720_500.0, half_width_m=460.0)
    assert outside_extent([passes, fails], dem) == ["box-460"]


def test_the_engine_rounds_the_extent_out_to_10_m(dem: NamedSnapshot) -> None:
    # 720055 - 50 = 720005 rounds down to 720000, on the DEM edge: inside.
    # 720049 - 50 = 719999 rounds down to 719990: outside, though only 1 m out.
    on_edge = endpoint("rounds-to-edge", 720_055.0)
    past_edge = endpoint("rounds-past-edge", 720_049.0)
    assert outside_extent([on_edge, past_edge], dem) == ["rounds-past-edge"]


@pytest.mark.parametrize(
    ("x_m", "y_m"),
    [(720_960.0, MID_Y_M), (720_500.0, 4_289_960.0), (720_500.0, 4_289_040.0)],
)
def test_every_edge_is_checked(dem: NamedSnapshot, x_m: float, y_m: float) -> None:
    assert outside_extent([endpoint("edge", x_m, y_m)], dem) == ["edge"]


def test_failures_come_back_in_input_order(dem: NamedSnapshot) -> None:
    points = [
        endpoint("b-out", 720_010.0),
        endpoint("a-in", 720_500.0),
        endpoint("c-out", 720_990.0),
    ]
    assert outside_extent(points, dem) == ["b-out", "c-out"]


def test_endpoints_that_pass_make_a_run_extent_the_engine_accepts(dem: NamedSnapshot) -> None:
    points = [
        endpoint("a", 720_060.0, 4_289_940.0, 5.0),
        endpoint("b", 720_940.0, 4_289_060.0, 3.0),
        endpoint("c", 720_500.0, MID_Y_M, 300.0),
        endpoint("d", 720_030.0),
    ]
    passing = [p for p in points if p.endpoint_id not in outside_extent(points, dem)]
    assert [p.endpoint_id for p in passing] == ["a", "b", "c"]
    check_sources({"dem": dem}, run_grid(passing, dem_resolution_m(dem)))


def test_the_check_is_on_bounds_not_values(tmp_path: Path) -> None:
    # A DEM that is all nodata still has its extent: the rule is the engine's
    # bounds test, not a test of values.
    heights = np.full((100, 100), -999999.0, dtype=np.float32)
    write_raster_snapshot(tmp_path, "dem-empty", {"dem.tif": (heights, 1.0, (WEST_M, NORTH_M))})
    empty = named(tmp_path, "dem-empty")["dem"]
    assert outside_extent([endpoint("inside", 720_050.0, 4_289_950.0)], empty) == []
