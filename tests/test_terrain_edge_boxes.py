"""Endpoint draws inside an `lnk_02` box given by its edges (ADR 0008).

The box is a rectangle in latitude and longitude, edges in 1e-7 degrees,
half-open. A draw is uniform between the edges in latitude and longitude and
converted to EPSG:26915 with pyproj, so it lies inside the box and the turn
between true north and grid north (about 1.6 degrees at x72y429) comes from
the conversion. Every box here is made up; the locations are tile x72y429's.
"""

import math

import numpy as np
import pytest
from pyproj import Proj, Transformer
from scipy import stats
from test_terrain_draws import FREQUENCY_MHZ, RUN_SEED, flat_profiles, free_space

from terrain.baselines import fspl_db
from terrain.draws import (
    GEOGRAPHIC_TO_UTM,
    EdgeBox,
    EndpointBox,
    draw_endpoints,
    sample_link,
)
from terrain.profile import XY

TO_GEOGRAPHIC = Transformer.from_crs(26915, 4326, always_xy=True)
UNIT_DEG = 1e-7
MANY = 10_000


def cell_box(easting_m: float, northing_m: float, bits: int = 16) -> EdgeBox:
    """The cell of `bits` precision holding a point, as the firmware truncates."""
    longitude_deg, latitude_deg = TO_GEOGRAPHIC.transform(easting_m, northing_m)
    width_i = 2 ** (32 - bits)
    south_i = math.floor(latitude_deg / UNIT_DEG / width_i) * width_i
    west_i = math.floor(longitude_deg / UNIT_DEG / width_i) * width_i
    return EdgeBox(
        south_i=south_i, north_i=south_i + width_i, west_i=west_i, east_i=west_i + width_i
    )


TX_CELL = cell_box(725_000.0, 4_285_000.0)
RX_CELL = cell_box(728_000.0, 4_288_000.0, bits=15)


def as_units(points: list[XY]) -> tuple[np.ndarray, np.ndarray]:
    """Latitude and longitude of EPSG:26915 points, in 1e-7 degrees."""
    xy = np.array(points)
    longitude_deg, latitude_deg = TO_GEOGRAPHIC.transform(xy[:, 0], xy[:, 1])
    return np.asarray(latitude_deg) / UNIT_DEG, np.asarray(longitude_deg) / UNIT_DEG


def ends(
    box_a: EdgeBox | EndpointBox, box_b: EdgeBox | EndpointBox, n: int = MANY
) -> tuple[list[XY], list[XY]]:
    pairs = draw_endpoints(box_a, box_b, n_draws=n, seed=RUN_SEED, floor_m=0.0)
    return [tx for tx, _ in pairs], [rx for _, rx in pairs]


def test_the_conversion_is_the_null_datum_shift_interface_names() -> None:
    assert GEOGRAPHIC_TO_UTM.description == (
        "Inverse of NAD83 to WGS 84 (1) + UTM zone 15N "
        "(with axis order normalized for visualization)"
    )


def test_the_centre_is_the_midpoint_of_the_edges_converted() -> None:
    latitude_deg = (TX_CELL.south_i + TX_CELL.north_i) / 2 * UNIT_DEG
    longitude_deg = (TX_CELL.west_i + TX_CELL.east_i) / 2 * UNIT_DEG
    expected = GEOGRAPHIC_TO_UTM.transform(longitude_deg, latitude_deg)

    assert TX_CELL.centre_xy_m == pytest.approx(expected, abs=1e-6)


def test_corners_are_south_west_south_east_north_east_north_west() -> None:
    corners = TX_CELL.corners_xy_m
    expected = [
        GEOGRAPHIC_TO_UTM.transform(longitude_i * UNIT_DEG, latitude_i * UNIT_DEG)
        for latitude_i, longitude_i in [
            (TX_CELL.south_i, TX_CELL.west_i),
            (TX_CELL.south_i, TX_CELL.east_i),
            (TX_CELL.north_i, TX_CELL.east_i),
            (TX_CELL.north_i, TX_CELL.west_i),
        ]
    ]
    for corner, want in zip(corners, expected, strict=True):
        assert corner == pytest.approx(want, abs=1e-6)
    west, south, east, north = TX_CELL.extent_m(floor_m=0.0)
    assert (west, south) == (min(c[0] for c in corners), min(c[1] for c in corners))
    assert (east, north) == (max(c[0] for c in corners), max(c[1] for c in corners))


@pytest.mark.parametrize("box", [TX_CELL, RX_CELL], ids=["16 bits", "15 bits"])
def test_every_draw_lies_inside_the_half_open_edges(box: EdgeBox) -> None:
    points, _ = ends(box, box)
    latitude_i, longitude_i = as_units(points)

    assert ((latitude_i >= box.south_i) & (latitude_i < box.north_i)).all()
    assert ((longitude_i >= box.west_i) & (longitude_i < box.east_i)).all()


def test_the_draws_follow_the_turn_between_true_and_grid_north() -> None:
    south_west, south_east, _, north_west = TX_CELL.corners_xy_m
    half_ns_m = math.dist(south_west, north_west) / 2
    half_ew_m = math.dist(south_west, south_east) / 2
    centre_x_m, centre_y_m = TX_CELL.centre_xy_m
    points, _ = ends(TX_CELL, TX_CELL)
    outside_unturned = [
        p
        for p in points
        if abs(p[0] - centre_x_m) > half_ew_m or abs(p[1] - centre_y_m) > half_ns_m
    ]
    # An unturned grid rectangle of the same size misses the box's corners.
    assert len(outside_unturned) > 50
    west_edge_deg = math.degrees(
        math.atan2(north_west[0] - south_west[0], north_west[1] - south_west[1])
    )
    longitude_deg, latitude_deg = TO_GEOGRAPHIC.transform(*TX_CELL.centre_xy_m)
    convergence_deg = Proj(26915).get_factors(longitude_deg, latitude_deg).meridian_convergence
    assert convergence_deg == pytest.approx(1.6, abs=0.1)
    # True north lies west of grid north here, so the west edge leans west.
    assert west_edge_deg == pytest.approx(-convergence_deg, abs=0.01)


def test_draws_are_uniform_in_latitude_and_longitude_and_centred() -> None:
    points, _ = ends(TX_CELL, TX_CELL)
    latitude_i, longitude_i = as_units(points)
    height_i = TX_CELL.north_i - TX_CELL.south_i
    width_i = TX_CELL.east_i - TX_CELL.west_i

    assert stats.kstest((latitude_i - TX_CELL.south_i) / height_i, "uniform").pvalue > 0.01
    assert stats.kstest((longitude_i - TX_CELL.west_i) / width_i, "uniform").pvalue > 0.01
    xy = np.array(points)
    standard_error = xy.std(axis=0) / math.sqrt(len(points))
    assert (np.abs(xy.mean(axis=0) - np.array(TX_CELL.centre_xy_m)) < 3 * standard_error).all()


def test_a_thin_merged_box_spreads_draws_along_its_long_side() -> None:
    # Two 32-bit fixes about 30 m apart on one parallel: a box 30 m by 1 cm.
    south_i, west_i = TX_CELL.south_i, TX_CELL.west_i
    thin = EdgeBox(south_i=south_i, north_i=south_i + 1, west_i=west_i, east_i=west_i + 3449)
    points, _ = ends(thin, thin)
    easting_m = np.array([p[0] for p in points])
    iqr_m = float(np.percentile(easting_m, 75) - np.percentile(easting_m, 25))

    assert iqr_m == pytest.approx(15.0, abs=0.5)
    square = EndpointBox(centre_xy_m=thin.centre_xy_m, half_width_m=0.0055)
    square_points, _ = ends(square, square)
    square_easting_m = np.array([p[0] for p in square_points])
    assert float(np.percentile(square_easting_m, 75) - np.percentile(square_easting_m, 25)) < 0.01


def test_a_square_end_draws_the_same_points_whatever_the_other_end() -> None:
    square = EndpointBox(centre_xy_m=(724_000.0, 4_289_500.0), half_width_m=25.0)
    beside_square = EndpointBox(centre_xy_m=TX_CELL.centre_xy_m, half_width_m=300.0)
    _, with_edges = ends(TX_CELL, square, n=16)
    _, with_square = ends(beside_square, square, n=16)

    assert with_edges == with_square


def test_one_draw_is_the_two_centres_and_a_seed_redraws_exactly() -> None:
    assert draw_endpoints(TX_CELL, RX_CELL, n_draws=1, seed=RUN_SEED, floor_m=0.0) == [
        (TX_CELL.centre_xy_m, RX_CELL.centre_xy_m)
    ]
    assert draw_endpoints(TX_CELL, RX_CELL, 16, seed=7, floor_m=0.0) == draw_endpoints(
        TX_CELL, RX_CELL, 16, seed=7, floor_m=0.0
    )


def test_flat_plane_median_equals_the_centre_result() -> None:
    far = cell_box(755_000.0, 4_285_000.0)
    summary = sample_link(
        "flat", TX_CELL, far, flat_profiles, free_space, run_seed=RUN_SEED, n_draws=16, floor_m=0.0
    )
    centre_m = math.dist(TX_CELL.centre_xy_m, far.centre_xy_m)

    assert summary.median("fspl_db") == pytest.approx(fspl_db(centre_m, FREQUENCY_MHZ), abs=0.1)
    iqr_db = summary.iqr("fspl_db")
    assert iqr_db is not None and iqr_db < 0.5


def test_reach_is_the_farthest_corner_from_the_centre() -> None:
    assert TX_CELL.reach_m == pytest.approx(
        max(math.dist(TX_CELL.centre_xy_m, c) for c in TX_CELL.corners_xy_m)
    )
    assert TX_CELL.reach_m == pytest.approx(462.3, abs=0.5)
    assert EndpointBox(centre_xy_m=(0.0, 0.0), half_width_m=10.0).reach_m == pytest.approx(
        10.0 * math.sqrt(2.0)
    )


def test_an_edge_box_always_has_area_and_a_point_has_none() -> None:
    assert TX_CELL.has_area
    assert EndpointBox(centre_xy_m=(0.0, 0.0), half_width_m=5.0).has_area
    assert not EndpointBox(centre_xy_m=(0.0, 0.0), half_width_m=0.0).has_area


@pytest.mark.parametrize(
    ("edges", "message"),
    [
        ((10, 10, 0, 5), "south_i"),
        ((0, 5, 7, 6), "west_i"),
        ((900_000_000, 900_000_001, 0, 5), "latitude"),
        ((0, 5, 1_800_000_000, 1_800_000_001), "longitude"),
        ((0.0, 5, 0, 5), "whole number"),
        ((True, 5, 0, 5), "whole number"),
    ],
)
def test_edges_are_checked(edges: tuple[object, ...], message: str) -> None:
    south, north, west, east = edges
    with pytest.raises(ValueError, match=message):
        EdgeBox(south_i=south, north_i=north, west_i=west, east_i=east)  # type: ignore[arg-type]


def test_first_edge_box_draw_is_pinned_for_a_fixed_seed() -> None:
    # The first of a box's two draw numbers moves longitude and the second
    # latitude; swapping them moves every edge-box draw by hundreds of metres.
    first = draw_endpoints(TX_CELL, RX_CELL, n_draws=16, seed=20261108, floor_m=0.0)[0]

    assert first[0] == pytest.approx((725456.6462583179, 4284488.885814736), abs=1e-6)
    assert first[1] == pytest.approx((727655.2808909145, 4287941.279346285), abs=1e-6)


def test_the_first_unit_moves_longitude_and_the_second_latitude() -> None:
    south_west, south_east, _, north_west = TX_CELL.corners_xy_m
    almost_one = math.nextafter(1.0, 0.0)
    corners = TX_CELL.place(
        np.array([[-1.0, -1.0], [almost_one, -1.0], [-1.0, almost_one]]), floor_m=0.0
    )

    assert corners[0] == pytest.approx(south_west, abs=1e-6)
    assert corners[1] == pytest.approx(south_east, abs=1e-3)
    assert corners[2] == pytest.approx(north_west, abs=1e-3)


@pytest.mark.parametrize(
    "edges",
    [
        (899_999_000, 900_000_000, 1_799_999_000, 1_800_000_000),
        (-900_000_000, -899_999_000, -1_800_000_000, -1_799_999_000),
    ],
    ids=["north and east limits", "south and west limits"],
)
def test_edges_on_the_limits_are_accepted(edges: tuple[int, int, int, int]) -> None:
    south, north, west, east = edges
    box = EdgeBox(south_i=south, north_i=north, west_i=west, east_i=east)
    assert (box.south_i, box.north_i, box.west_i, box.east_i) == edges


@pytest.mark.parametrize(
    "edges",
    [
        (386_859_008, 386_793_472, -904_134_656, -904_069_120),
        (386_793_472, 386_859_008, -904_069_120, -904_134_656),
        (386_793_472.0, 386_859_008, -904_134_656, -904_069_120),
    ],
    ids=["south above north", "west above east", "not whole"],
)
def test_an_edge_error_names_the_rule_and_no_edge_value(edges: tuple[object, ...]) -> None:
    # The message reaches the engine's and prepare's logs beside a pseudonym,
    # so it must not hold a cell's edges.
    south, north, west, east = edges
    with pytest.raises(ValueError) as raised:
        EdgeBox(south_i=south, north_i=north, west_i=west, east_i=east)  # type: ignore[arg-type]
    message = str(raised.value)
    for value in edges:
        assert str(int(value)) not in message  # type: ignore[call-overload]
