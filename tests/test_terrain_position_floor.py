"""The GPS error floor under endpoint draws (Casey, issue #55 comments 6962700456 and 7042577618).

A box smaller than the receiver's own error claims more than a fix can know.
The draws therefore never use a half-width below the floor: a square's
half-width is raised to it, and an `lnk_02` edge box is widened about its
centre on each axis below it, in latitude and longitude with `pos_01`'s
WGS84 radii at the box centre (Casey's (A)), then converted to EPSG:26915 as
every edge-box draw is. Exact points (half-width 0) are model points, not
fixes, and stay exact. A floor of 0 turns it off. Every box here is made up.
"""

import math

import numpy as np
import pytest
from pyproj import Geod, Transformer
from test_terrain_draws import RUN_SEED
from test_terrain_edge_boxes import TX_CELL, cell_box

from terrain.boxes import EdgeBox, EndpointBox, floor_units
from terrain.draws import draw_endpoints
from terrain.profile import XY

TO_GEOGRAPHIC = Transformer.from_crs(26915, 4326, always_xy=True)
FLOOR_M = 5.0
MANY = 10_000


def tx_points(box: EdgeBox | EndpointBox, floor_m: float, n: int = MANY) -> list[XY]:
    point = EndpointBox(centre_xy_m=(724_000.0, 4_289_500.0), half_width_m=0.0)
    return [tx for tx, _ in draw_endpoints(box, point, n, seed=RUN_SEED, floor_m=floor_m)]


def iqr(values: np.ndarray) -> float:
    return float(np.percentile(values, 75) - np.percentile(values, 25))


def test_a_square_below_the_floor_draws_as_a_square_of_the_floor() -> None:
    small = EndpointBox(centre_xy_m=(725_000.0, 4_285_000.0), half_width_m=1.0)
    floored = EndpointBox(centre_xy_m=(725_000.0, 4_285_000.0), half_width_m=FLOOR_M)

    assert tx_points(small, FLOOR_M, n=16) == tx_points(floored, 0.0, n=16)
    offsets = np.abs(np.array(tx_points(small, FLOOR_M)) - np.array(small.centre_xy_m))
    assert offsets.max() <= FLOOR_M
    assert offsets.max() > 0.99 * FLOOR_M


def test_a_square_at_or_above_the_floor_draws_as_before() -> None:
    box = EndpointBox(centre_xy_m=(725_000.0, 4_285_000.0), half_width_m=20.0)
    assert tx_points(box, FLOOR_M, n=16) == tx_points(box, 0.0, n=16)


def test_an_exact_point_stays_exact_under_the_floor() -> None:
    point = EndpointBox(centre_xy_m=(725_000.0, 4_285_000.0), half_width_m=0.0)
    assert set(tx_points(point, FLOOR_M, n=16)) == {point.centre_xy_m}


def test_a_16_bit_cell_draws_as_before() -> None:
    assert tx_points(TX_CELL, FLOOR_M, n=16) == tx_points(TX_CELL, 0.0, n=16)


def test_the_floor_in_edge_units_spans_twice_the_floor_on_the_ellipsoid() -> None:
    # An independent check: pyproj's WGS84 geodesic across the centre plus and
    # minus the floor, rather than pos_01's formula the code itself uses.
    latitude_deg, longitude_deg = 38.69, -90.41
    ns_units_i, ew_units_i = floor_units(latitude_deg, FLOOR_M)
    geod = Geod(ellps="WGS84")
    half_ns_deg, half_ew_deg = ns_units_i * 1e-7, ew_units_i * 1e-7
    _, _, ns_m = geod.inv(
        longitude_deg, latitude_deg - half_ns_deg, longitude_deg, latitude_deg + half_ns_deg
    )
    _, _, ew_m = geod.inv(
        longitude_deg - half_ew_deg, latitude_deg, longitude_deg + half_ew_deg, latitude_deg
    )

    assert ns_m == pytest.approx(2 * FLOOR_M, abs=1e-6)
    assert ew_m == pytest.approx(2 * FLOOR_M, abs=1e-6)


def test_a_32_bit_cell_is_widened_to_the_floor_on_both_axes() -> None:
    cell = cell_box(725_000.0, 4_285_000.0, bits=32)
    points = np.array(tx_points(cell, FLOOR_M))
    offsets = points - np.array(cell.centre_xy_m)

    # Uniform over 10 m on each axis: an interquartile range of 5 m.
    assert iqr(offsets[:, 0]) == pytest.approx(FLOOR_M, abs=0.25)
    assert iqr(offsets[:, 1]) == pytest.approx(FLOOR_M, abs=0.25)
    assert np.hypot(offsets[:, 0], offsets[:, 1]).max() <= FLOOR_M * math.sqrt(2) + 0.01
    longitude_deg, latitude_deg = TO_GEOGRAPHIC.transform(points[:, 0], points[:, 1])
    latitude_i = np.asarray(latitude_deg) * 1e7
    longitude_i = np.asarray(longitude_deg) * 1e7
    inside_cell = (
        (latitude_i >= cell.south_i)
        & (latitude_i < cell.north_i)
        & (longitude_i >= cell.west_i)
        & (longitude_i < cell.east_i)
    )
    # By design the floor draws outside a cell smaller than itself.
    assert inside_cell.mean() < 0.01


def test_a_thin_merged_box_is_widened_on_its_short_axis_only() -> None:
    south_i, west_i = TX_CELL.south_i, TX_CELL.west_i
    thin = EdgeBox(south_i=south_i, north_i=south_i + 1, west_i=west_i, east_i=west_i + 3449)
    offsets = np.array(tx_points(thin, FLOOR_M)) - np.array(thin.centre_xy_m)

    assert iqr(offsets[:, 0]) == pytest.approx(15.0, abs=0.5)
    assert iqr(offsets[:, 1]) == pytest.approx(FLOOR_M, abs=0.25)


def test_the_extent_with_the_floor_holds_every_floored_draw() -> None:
    cell = cell_box(725_000.0, 4_285_000.0, bits=32)
    west, south, east, north = cell.extent_m(floor_m=FLOOR_M)
    points = np.array(tx_points(cell, FLOOR_M))

    assert east - west == pytest.approx(2 * FLOOR_M, abs=0.5)
    assert north - south == pytest.approx(2 * FLOOR_M, abs=0.5)
    assert (points[:, 0] >= west - 1e-6).all() and (points[:, 0] <= east + 1e-6).all()
    assert (points[:, 1] >= south - 1e-6).all() and (points[:, 1] <= north + 1e-6).all()
    small = EndpointBox(centre_xy_m=(725_000.0, 4_285_000.0), half_width_m=1.0)
    assert small.extent_m(floor_m=FLOOR_M) == (724_995.0, 4_284_995.0, 725_005.0, 4_285_005.0)


@pytest.mark.parametrize("floor_m", [-1.0, math.nan, math.inf])
def test_a_floor_must_be_a_finite_number_of_at_least_0(floor_m: float) -> None:
    with pytest.raises(ValueError, match="floor_m"):
        tx_points(TX_CELL, floor_m, n=16)


def test_the_drawn_reach_is_the_reach_of_the_floored_box() -> None:
    # crossing.md compares this with the distance to the nearest tall cell.
    cell = cell_box(725_000.0, 4_285_000.0, bits=32)
    assert cell.drawn_reach_m(FLOOR_M) == pytest.approx(FLOOR_M * math.sqrt(2), abs=0.01)
    assert TX_CELL.drawn_reach_m(FLOOR_M) == TX_CELL.reach_m
    small = EndpointBox(centre_xy_m=(725_000.0, 4_285_000.0), half_width_m=1.0)
    assert small.drawn_reach_m(FLOOR_M) == pytest.approx(FLOOR_M * math.sqrt(2))
    point = EndpointBox(centre_xy_m=(725_000.0, 4_285_000.0), half_width_m=0.0)
    assert point.drawn_reach_m(FLOOR_M) == 0.0
