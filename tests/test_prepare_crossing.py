"""The crossing check: which run paths pass near a cell more than 60 m above the ground.

A tall cell is a DSM cell whose height minus the DEM's at the same place
exceeds the threshold. A path is the straight line between a site's and a
receiver's box centres, EPSG:26915; its distance to a cell is to the cell's
square, not its centre, and a path crosses when that distance is at most 10 m.
"""

from pathlib import Path

import numpy as np
import pytest
from prepare_snapshots import named, write_raster_snapshot

from engine.points import Endpoint
from engine.prepare.crossing import path_crossings, tall_cells
from terrain.draws import EndpointBox

WEST_M, NORTH_M = 720_000.0, 4_290_000.0
NODATA = -999999.0


def cell(row: int, col: int, west_m: float = WEST_M, north_m: float = NORTH_M) -> tuple[float, ...]:
    """Bounds (west, south, east, north) of a 1 m pixel of a raster at the given corner."""
    return (west_m + col, north_m - row - 1.0, west_m + col + 1.0, north_m - row)


def as_set(bounds: np.ndarray) -> set[tuple[float, ...]]:
    return {tuple(float(v) for v in row) for row in bounds}


def test_cells_above_the_threshold_are_found_across_window_borders(tmp_path: Path) -> None:
    dem = np.full((600, 600), 100.0, dtype=np.float32)
    dsm = dem + 5.0
    dsm[100:103, 509:515] = 170.0
    dsm[300, 300] = 160.0
    dsm[301, 300] = 160.5
    dsm[400, 400] = NODATA
    write_raster_snapshot(tmp_path, "dem", {"dem.tif": (dem, 1.0, (WEST_M, NORTH_M))})
    write_raster_snapshot(tmp_path, "dsm", {"dsm.tif": (dsm, 1.0, (WEST_M, NORTH_M))})
    snapshots = named(tmp_path, "dem", "dsm")
    found = tall_cells(snapshots["dem"], snapshots["dsm"], threshold_m=60.0)
    block = {cell(r, c) for r in range(100, 103) for c in range(509, 515)}
    assert found.shape == (19, 4)
    assert as_set(found) == block | {cell(301, 300)}


def test_dem_and_dsm_on_offset_grids_are_compared_at_the_same_place(tmp_path: Path) -> None:
    # The x72y429 DSM starts 6 m west and 6 m north of whole tile edges. A pit
    # in the DEM under a flat DSM is the one place where the difference is
    # large, so a misaligned comparison would put the cell elsewhere or lose it.
    dem = np.full((200, 200), 100.0, dtype=np.float32)
    dem[50, 70] = 30.0
    dsm = np.full((212, 212), 95.0, dtype=np.float32)
    write_raster_snapshot(tmp_path, "dem", {"dem.tif": (dem, 1.0, (WEST_M, NORTH_M))})
    write_raster_snapshot(tmp_path, "dsm", {"dsm.tif": (dsm, 1.0, (WEST_M - 6.0, NORTH_M + 6.0))})
    snapshots = named(tmp_path, "dem", "dsm")
    found = tall_cells(snapshots["dem"], snapshots["dsm"], threshold_m=60.0)
    assert as_set(found) == {cell(50, 70)}


def test_a_dem_gap_under_a_tall_surface_is_not_a_tall_cell(tmp_path: Path) -> None:
    # No ground height means no height above ground: the cell is never tall,
    # whatever the DSM says, and its neighbours still count.
    dem = np.full((50, 50), 100.0, dtype=np.float32)
    dem[10, 10] = NODATA
    dsm = dem.copy()
    dsm[10, 10:12] = 300.0
    write_raster_snapshot(tmp_path, "dem", {"dem.tif": (dem, 1.0, (WEST_M, NORTH_M))})
    write_raster_snapshot(tmp_path, "dsm", {"dsm.tif": (dsm, 1.0, (WEST_M, NORTH_M))})
    snapshots = named(tmp_path, "dem", "dsm")
    found = tall_cells(snapshots["dem"], snapshots["dsm"])
    assert as_set(found) == {cell(10, 11)}


def test_no_tall_cell_gives_an_empty_list(tmp_path: Path) -> None:
    dem = np.full((50, 50), 100.0, dtype=np.float32)
    write_raster_snapshot(tmp_path, "dem", {"dem.tif": (dem, 1.0, (WEST_M, NORTH_M))})
    write_raster_snapshot(tmp_path, "dsm", {"dsm.tif": (dem + 20.0, 1.0, (WEST_M, NORTH_M))})
    snapshots = named(tmp_path, "dem", "dsm")
    assert tall_cells(snapshots["dem"], snapshots["dsm"]).shape == (0, 4)


TALL = np.array([[725_100.0, 4_285_100.0, 725_101.0, 4_285_101.0]])
"""One tall cell, 1 m square, EPSG:26915."""


def point(name: str, x_m: float, y_m: float, half_width_m: float = 0.0) -> Endpoint:
    return Endpoint(
        endpoint_id=name, box=EndpointBox(centre_xy_m=(x_m, y_m), half_width_m=half_width_m)
    )


def one_path(
    tx: tuple[float, float], rx: tuple[float, float], cells: np.ndarray = TALL
) -> tuple[float | None, bool]:
    (crossing,) = path_crossings([point("site", *tx)], [point("rx", *rx)], cells)
    return crossing.distance_m, crossing.crosses


def test_a_path_through_a_tall_cell_crosses_at_distance_0() -> None:
    assert one_path((725_000.0, 4_285_100.5), (725_200.0, 4_285_100.5)) == (0.0, True)


@pytest.mark.parametrize(
    ("y_m", "distance_m", "crosses"),
    [(4_285_111.0, 10.0, True), (4_285_111.5, 10.5, False), (4_285_090.0, 10.0, True)],
)
def test_a_path_passing_beside_the_cell_crosses_within_10_m(
    y_m: float, distance_m: float, crosses: bool
) -> None:
    assert one_path((725_000.0, y_m), (725_200.0, y_m)) == pytest.approx((distance_m, crosses))


@pytest.mark.parametrize(
    ("end_x_m", "distance_m", "crosses"), [(725_090.0, 10.0, True), (725_089.0, 11.0, False)]
)
def test_distance_is_to_the_cell_edge_not_its_centre(
    end_x_m: float, distance_m: float, crosses: bool
) -> None:
    # The cell centre is 0.5 m further: 10.5 m from the end at 725090, which
    # would read as no crossing.
    assert one_path((725_000.0, 4_285_100.5), (end_x_m, 4_285_100.5)) == pytest.approx(
        (distance_m, crosses)
    )


def test_a_path_ending_past_the_cell_is_measured_to_its_nearest_point() -> None:
    # Diagonal path whose nearest approach to the cell's south-west corner is
    # the perpendicular from it: corner (725100, 4285100), line x + y = c.
    distance_m, crosses = one_path((725_000.0, 4_285_190.0), (725_190.0, 4_285_000.0))
    assert distance_m == pytest.approx(10.0 / np.sqrt(2.0))
    assert crosses


def test_the_nearest_of_several_cells_counts() -> None:
    cells = np.vstack([TALL, [[725_100.0, 4_285_130.0, 725_101.0, 4_285_131.0]]])
    assert one_path((725_000.0, 4_285_125.0), (725_200.0, 4_285_125.0), cells) == (5.0, True)


def test_a_zero_length_path_is_measured_from_its_one_point() -> None:
    assert one_path((725_100.5, 4_285_095.0), (725_100.5, 4_285_095.0)) == (5.0, True)


def test_without_tall_cells_no_path_crosses() -> None:
    assert one_path((725_000.0, 4_285_100.5), (725_200.0, 4_285_100.5), np.empty((0, 4))) == (
        None,
        False,
    )


def test_every_site_and_receiver_pair_is_checked_site_by_site() -> None:
    sites = [point("s1", 725_000.0, 4_285_100.5), point("s2", 725_000.0, 4_285_300.0)]
    receivers = [point("r1", 725_200.0, 4_285_100.5), point("r2", 725_200.0, 4_285_300.0)]
    crossings = path_crossings(sites, receivers, TALL)
    assert [(c.site_id, c.rx_id, c.crosses) for c in crossings] == [
        ("s1", "r1", True),
        ("s1", "r2", False),
        ("s2", "r1", False),
        ("s2", "r2", False),
    ]


def test_box_half_widths_do_not_move_the_path() -> None:
    # The check reads box centres, as predictions.parquet measures its paths.
    (crossing,) = path_crossings(
        [point("site", 725_000.0, 4_285_120.0, 300.0)],
        [point("rx", 725_200.0, 4_285_120.0, 300.0)],
        TALL,
    )
    assert crossing.distance_m == pytest.approx(19.0)
    assert not crossing.crosses
