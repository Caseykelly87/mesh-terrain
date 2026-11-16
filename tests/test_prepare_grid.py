"""The made-up receiver grid of a USGS 1 m tile run, and the tile name it comes from.

USGS names a 10 km 1 m DEM tile xAyB: it covers eastings A to A + 1 and
northings B - 1 to B in 10 km units of EPSG:26915. The grid puts a receiver
every 500 m starting 250 m inside the tile edges, so x72y429 holds 20 by 20
points from 720250 to 729750 E and 4280250 to 4289750 N.
"""

import pytest

from engine.prepare.tile import grid_receivers, parse_tile


def test_a_tile_name_gives_its_10_km_square() -> None:
    tile = parse_tile("x72y429")
    assert (tile.west_m, tile.south_m, tile.east_m, tile.north_m) == (
        720_000.0,
        4_280_000.0,
        730_000.0,
        4_290_000.0,
    )
    assert tile.name == "x72y429"


@pytest.mark.parametrize("name", ["", "x72", "y429", "X72Y429", "x72y429a", "x-1y429", "72y429"])
def test_a_name_that_is_not_a_tile_is_refused(name: str) -> None:
    with pytest.raises(ValueError, match="tile"):
        parse_tile(name)


def test_the_x72y429_grid_has_400_points_every_500_m_from_250_m_inside() -> None:
    points = grid_receivers(parse_tile("x72y429"))
    xs = sorted({p.box.centre_xy_m[0] for p in points})
    ys = sorted({p.box.centre_xy_m[1] for p in points})
    assert len(points) == 400
    assert xs == [720_250.0 + 500.0 * i for i in range(20)]
    assert ys == [4_280_250.0 + 500.0 * i for i in range(20)]
    assert all(p.box.half_width_m == 0.0 for p in points)


def test_grid_ids_count_rows_from_the_north_and_columns_from_the_west() -> None:
    points = {p.endpoint_id: p.box.centre_xy_m for p in grid_receivers(parse_tile("x72y429"))}
    assert len(points) == 400
    assert points["grid-r00c00"] == (720_250.0, 4_289_750.0)
    assert points["grid-r00c19"] == (729_750.0, 4_289_750.0)
    assert points["grid-r19c00"] == (720_250.0, 4_280_250.0)
    assert points["grid-r07c03"] == (721_750.0, 4_286_250.0)


def test_grid_points_come_row_by_row_from_the_north_west_corner() -> None:
    ids = [p.endpoint_id for p in grid_receivers(parse_tile("x72y429"))]
    assert ids[:3] == ["grid-r00c00", "grid-r00c01", "grid-r00c02"]
    assert ids[20] == "grid-r01c00"
    assert ids[-1] == "grid-r19c19"


def test_a_spacing_that_does_not_fit_the_tile_is_refused() -> None:
    with pytest.raises(ValueError, match="spacing"):
        grid_receivers(parse_tile("x72y429"), spacing_m=300.0)
