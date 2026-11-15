"""P.1812's 10 m profile from fixed 10 by 10 pixel cells (ADR 0006 items 6 to 9)."""

from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest
import rasterio
from terrain_dem import ORIGIN_X_M, ORIGIN_Y_M, cell_centre_xy_m, open_layers, write_dem

from terrain.cells import FineLayers, cell_profile
from terrain.errors import RefusedDrawError

F32 = npt.NDArray[np.float32]


def cell_mean(pixels: npt.NDArray[np.floating], cell_row: int, cell_col: int) -> float:
    block = pixels[10 * cell_row : 10 * cell_row + 10, 10 * cell_col : 10 * cell_col + 10]
    return float(block.astype(np.float64).mean())


def rough_layers(tmp_path: Path, rows: int = 60, cols: int = 300) -> tuple[Path, Path, F32, F32]:
    """A DEM of random heights and a DSM with lidar noise, roofs and trees on it."""
    rng = np.random.default_rng(11)
    dem = rng.uniform(100.0, 110.0, size=(rows, cols)).astype(np.float32)
    above = rng.choice([-0.2, 0.0, 0.1, 5.0, 9.0], size=(rows, cols)).astype(np.float32)
    dsm = dem + above
    return write_dem(tmp_path / "dem.tif", dem), write_dem(tmp_path / "dsm.tif", dsm), dem, dsm


def test_ground_and_clutter_are_cell_means_taken_separately(tmp_path: Path) -> None:
    dem_path, dsm_path, dem, dsm = rough_layers(tmp_path)
    clutter = np.maximum(dsm.astype(np.float64) - dem.astype(np.float64), 0.0)

    with open_layers(dem_path, dsm=dsm_path) as layers:
        result = cell_profile(
            layers,
            cell_centre_xy_m(2, 1),
            cell_centre_xy_m(2, 25),
            tx_ground_m=101.0,
            rx_ground_m=102.0,
        )

    profile = result.profile
    assert profile.distance_m.size == 240 // 10 + 1
    assert np.allclose(np.diff(profile.distance_m), 10.0)
    # Interior sample k lies on the centre of cell (2, 1 + k).
    for k in range(1, 24):
        assert profile.ground_m[k] == pytest.approx(cell_mean(dem, 2, 1 + k), abs=1e-6)
        assert profile.clutter_m is not None
        assert profile.clutter_m[k] == pytest.approx(cell_mean(clutter, 2, 1 + k), abs=1e-6)


def test_the_two_ends_take_the_given_ground_and_no_clutter(tmp_path: Path) -> None:
    dem_path, dsm_path, _, _ = rough_layers(tmp_path)

    with open_layers(dem_path, dsm=dsm_path) as layers:
        result = cell_profile(
            layers,
            cell_centre_xy_m(2, 1),
            cell_centre_xy_m(2, 25),
            tx_ground_m=101.25,
            rx_ground_m=107.5,
        )

    assert result.profile.ground_m[0] == 101.25
    assert result.profile.ground_m[-1] == 107.5
    assert result.profile.clutter_m is not None
    assert result.profile.clutter_m[0] == 0.0
    assert result.profile.clutter_m[-1] == 0.0


def test_cells_sit_on_the_fixed_grid_whatever_the_window(tmp_path: Path) -> None:
    dem_path, dsm_path, dem, _ = rough_layers(tmp_path)
    tx, rx = cell_centre_xy_m(2, 1), cell_centre_xy_m(2, 25)

    with open_layers(dem_path, dsm=dsm_path) as layers:
        wide = cell_profile(layers, tx, rx, tx_ground_m=100.0, rx_ground_m=100.0)
        narrow = cell_profile(
            layers, tx, rx, tx_ground_m=100.0, rx_ground_m=100.0, max_window_cells=6
        )
        down = cell_profile(
            layers,
            cell_centre_xy_m(0, 7),
            cell_centre_xy_m(5, 7),
            tx_ground_m=100.0,
            rx_ground_m=100.0,
        )

    assert np.array_equal(wide.profile.ground_m, narrow.profile.ground_m)
    assert wide.profile.clutter_m is not None and narrow.profile.clutter_m is not None
    assert np.array_equal(wide.profile.clutter_m, narrow.profile.clutter_m)
    # A north-south path through cell (2, 7) reads the same cell mean as the east-west one.
    assert down.profile.ground_m[2] == pytest.approx(wide.profile.ground_m[6], abs=1e-9)
    assert down.profile.ground_m[2] == pytest.approx(cell_mean(dem, 2, 7), abs=1e-6)


def test_points_between_cell_centres_are_bilinear(tmp_path: Path) -> None:
    cell_values = 100.0 + np.arange(30, dtype=np.float64) ** 2
    dem = np.repeat(np.tile(cell_values, (6, 1)), 10, axis=0).repeat(10, axis=1)
    dem_path = write_dem(tmp_path / "dem.tif", dem.astype(np.float32))
    canopy_path = write_dem(tmp_path / "canopy.tif", np.zeros_like(dem, dtype=np.float32))
    # The path starts on the edge between cells 0 and 1, so every 10 m sample
    # falls halfway between two cell centres.
    tx = (ORIGIN_X_M + 10.0, ORIGIN_Y_M - 25.0)
    rx = (ORIGIN_X_M + 250.0, ORIGIN_Y_M - 25.0)

    with open_layers(dem_path, canopy=canopy_path) as layers:
        result = cell_profile(layers, tx, rx, tx_ground_m=0.0, rx_ground_m=0.0)

    for k in range(1, 24):
        expected = (cell_values[k] + cell_values[k + 1]) / 2.0
        assert result.profile.ground_m[k] == pytest.approx(expected, abs=1e-4)


def test_a_cell_holding_a_dem_gap_refuses_the_draw_as_empty_cell(tmp_path: Path) -> None:
    dem = np.full((60, 300), 100.0, dtype=np.float32)
    dem[21, 52] = -999999.0
    dem_path = write_dem(tmp_path / "dem.tif", dem)
    dsm_path = write_dem(tmp_path / "dsm.tif", dem.copy())

    with open_layers(dem_path, dsm=dsm_path) as layers, pytest.raises(RefusedDrawError) as caught:
        # The 1 m line runs 3.5 m from the gap; the 10 m profile reads its cell (2, 5).
        cell_profile(
            layers,
            cell_centre_xy_m(2, 1),
            cell_centre_xy_m(2, 25),
            tx_ground_m=100.0,
            rx_ground_m=100.0,
        )

    assert caught.value.reason == "empty_cell"


def test_a_cell_partly_beyond_the_raster_edge_is_empty(tmp_path: Path) -> None:
    dem = np.full((40, 209), 100.0, dtype=np.float32)
    dem_path = write_dem(tmp_path / "dem.tif", dem)
    canopy_path = write_dem(tmp_path / "canopy.tif", np.zeros_like(dem))
    # Cell column 20 holds pixel columns 200 to 208 only; the last interior
    # sample reads it.
    rx = (ORIGIN_X_M + 208.5, ORIGIN_Y_M - 25.0)

    with (
        open_layers(dem_path, canopy=canopy_path) as layers,
        pytest.raises(RefusedDrawError) as caught,
    ):
        cell_profile(layers, cell_centre_xy_m(2, 1), rx, tx_ground_m=100.0, rx_ground_m=100.0)

    assert caught.value.reason == "empty_cell"


def test_surface_and_canopy_gaps_are_filled_and_counted_not_refused(tmp_path: Path) -> None:
    dem = np.full((60, 300), 100.0, dtype=np.float32)
    dsm = dem + 3.0
    canopy = np.zeros_like(dem)
    dsm[21, 52] = dsm[21, 53] = -999999.0
    dsm[55, 55] = -999999.0  # cell (5, 5): the profile does not read it
    canopy[22, 60] = -999999.0
    paths = {
        name: write_dem(tmp_path / f"{name}.tif", grid)
        for name, grid in (("dem", dem), ("dsm", dsm), ("canopy", canopy))
    }

    with open_layers(paths["dem"], dsm=paths["dsm"], canopy=paths["canopy"]) as layers:
        result = cell_profile(
            layers,
            cell_centre_xy_m(2, 1),
            cell_centre_xy_m(2, 25),
            tx_ground_m=100.0,
            rx_ground_m=100.0,
        )

    assert result.profile.clutter_m is not None
    # Two of cell (2, 5)'s 100 pixels are filled with 0 instead of 3 m.
    assert result.profile.clutter_m[4] == pytest.approx(3.0 * 98 / 100)
    assert result.profile.clutter_m[5] == pytest.approx(3.0)
    assert result.dsm_filled_pixels == 2


def test_without_a_dsm_no_pixel_counts_as_filled(tmp_path: Path) -> None:
    dem = np.full((40, 300), 100.0, dtype=np.float32)
    dem_path = write_dem(tmp_path / "dem.tif", dem)
    canopy_path = write_dem(tmp_path / "canopy.tif", np.zeros_like(dem))

    with open_layers(dem_path, canopy=canopy_path) as layers:
        result = cell_profile(
            layers,
            cell_centre_xy_m(2, 1),
            cell_centre_xy_m(2, 25),
            tx_ground_m=100.0,
            rx_ground_m=100.0,
        )

    assert result.dsm_filled_pixels is None


def test_worked_example_cell_half_roof_half_tree_gives_11_5_m(tmp_path: Path) -> None:
    dem = np.full((40, 300), 150.0, dtype=np.float32)
    dsm = dem.copy()
    canopy = np.zeros_like(dem)
    roof = (np.arange(300) % 10) < 5
    dsm[:, roof] += 8.0
    dsm[:, ~roof] += 2.0
    canopy[:, ~roof] = 15.0
    paths = {
        name: write_dem(tmp_path / f"{name}.tif", grid)
        for name, grid in (("dem", dem), ("dsm", dsm), ("canopy", canopy))
    }

    with open_layers(paths["dem"], dsm=paths["dsm"], canopy=paths["canopy"]) as layers:
        result = cell_profile(
            layers,
            cell_centre_xy_m(1, 1),
            cell_centre_xy_m(2, 27),
            tx_ground_m=150.0,
            rx_ground_m=150.0,
        )

    assert result.profile.clutter_m is not None
    assert np.allclose(result.profile.clutter_m[1:-1], 11.5)
    assert np.allclose(result.profile.ground_m, 150.0)


def test_a_path_shorter_than_one_step_reads_no_cell(tmp_path: Path) -> None:
    dem = np.full((40, 40), 100.0, dtype=np.float32)
    dem_path = write_dem(tmp_path / "dem.tif", dem)
    canopy_path = write_dem(tmp_path / "canopy.tif", np.zeros_like(dem))
    tx = (ORIGIN_X_M + 12.0, ORIGIN_Y_M - 12.0)
    rx = (ORIGIN_X_M + 20.0, ORIGIN_Y_M - 12.0)

    with open_layers(dem_path, canopy=canopy_path) as layers:
        result = cell_profile(layers, tx, rx, tx_ground_m=99.0, rx_ground_m=98.0)

    assert result.profile.distance_m.tolist() == pytest.approx([0.0, 8.0])
    assert result.profile.ground_m.tolist() == [99.0, 98.0]
    assert result.profile.clutter_m is not None
    assert result.profile.clutter_m.tolist() == [0.0, 0.0]


def test_layers_need_a_dsm_or_a_canopy(tmp_path: Path) -> None:
    dem_path = write_dem(tmp_path / "dem.tif", np.zeros((20, 20), dtype=np.float32))

    with rasterio.open(dem_path) as dem, pytest.raises(ValueError, match="DSM or a canopy"):
        FineLayers(dem=dem, dsm=None, canopy=None)


def test_layers_must_share_the_dem_grid(tmp_path: Path) -> None:
    dem_path = write_dem(tmp_path / "dem.tif", np.zeros((20, 20), dtype=np.float32))
    wider = write_dem(tmp_path / "wider.tif", np.zeros((20, 30), dtype=np.float32))

    with pytest.raises(ValueError, match="grid"), open_layers(dem_path, dsm=wider):
        pass


def test_layers_must_have_1_m_pixels(tmp_path: Path) -> None:
    coarse = np.zeros((20, 20), dtype=np.float32)
    dem_path = write_dem(tmp_path / "dem.tif", coarse, resolution_m=2.0)
    canopy_path = write_dem(tmp_path / "canopy.tif", coarse, resolution_m=2.0)

    with pytest.raises(ValueError, match="1 m"), open_layers(dem_path, canopy=canopy_path):
        pass


def test_an_end_without_1_m_ground_is_a_dem_gap(tmp_path: Path) -> None:
    dem = np.full((40, 300), 100.0, dtype=np.float32)
    dem_path = write_dem(tmp_path / "dem.tif", dem)
    canopy_path = write_dem(tmp_path / "canopy.tif", np.zeros_like(dem))

    with (
        open_layers(dem_path, canopy=canopy_path) as layers,
        pytest.raises(RefusedDrawError) as caught,
    ):
        cell_profile(
            layers,
            cell_centre_xy_m(2, 1),
            cell_centre_xy_m(2, 25),
            tx_ground_m=100.0,
            rx_ground_m=float("nan"),
        )

    assert caught.value.reason == "dem_gap"


def test_a_window_too_small_for_its_margin_is_rejected(tmp_path: Path) -> None:
    dem = np.full((40, 300), 100.0, dtype=np.float32)
    dem_path = write_dem(tmp_path / "dem.tif", dem)
    canopy_path = write_dem(tmp_path / "canopy.tif", np.zeros_like(dem))

    with (
        open_layers(dem_path, canopy=canopy_path) as layers,
        pytest.raises(ValueError, match="small"),
    ):
        cell_profile(
            layers,
            cell_centre_xy_m(2, 1),
            cell_centre_xy_m(2, 25),
            tx_ground_m=100.0,
            rx_ground_m=100.0,
            max_window_cells=5,
        )
