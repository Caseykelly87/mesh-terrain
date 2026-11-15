"""Profile sampler on synthetic DEMs: windowed reads, bilinear values, extent and nodata."""

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rasterio.io
from terrain_dem import centre_xy_m, sample_at, write_dem

from terrain.errors import OutOfExtentError
from terrain.profile import PathProfile


def flat_dem(tmp_path: Path, height_m: float = 100.0, rows: int = 50, cols: int = 1200) -> Path:
    return write_dem(tmp_path / "flat.tif", np.full((rows, cols), height_m, dtype=np.float32))


def test_sample_count_is_distance_over_step_plus_one(tmp_path: Path) -> None:
    dem = flat_dem(tmp_path)
    tx, rx = centre_xy_m(25, 10), centre_xy_m(25, 1010)

    one_m = sample_at(dem, tx, rx, step_m=1.0)
    ten_m = sample_at(dem, tx, rx, step_m=10.0)

    assert one_m.distance_m.size == 1000 + 1
    assert ten_m.distance_m.size == 1000 // 10 + 1
    assert ten_m.distance_m[0] == 0.0
    assert ten_m.distance_m[-1] == pytest.approx(1000.0)
    assert np.allclose(np.diff(ten_m.distance_m), 10.0)


def test_step_that_does_not_divide_the_length_stays_uniform_and_ends_on_the_endpoint(
    tmp_path: Path,
) -> None:
    dem = flat_dem(tmp_path)
    profile = sample_at(dem, centre_xy_m(25, 10), centre_xy_m(25, 1015), step_m=10.0)

    assert profile.distance_m.size == 101 + 1
    assert profile.distance_m[-1] == pytest.approx(1005.0)
    assert np.allclose(np.diff(profile.distance_m), 1005.0 / 101)
    assert profile.step_m <= 10.0


def test_endpoint_elevations_equal_the_dem_at_those_cells(tmp_path: Path) -> None:
    rng = np.random.default_rng(7)
    heights = rng.uniform(120.0, 220.0, size=(400, 500)).astype(np.float32)
    dem = write_dem(tmp_path / "random.tif", heights)

    profile = sample_at(dem, centre_xy_m(5, 10), centre_xy_m(305, 410), step_m=1.0)

    assert profile.distance_m[-1] == pytest.approx(500.0)
    assert profile.ground_m[0] == pytest.approx(float(heights[5, 10]), abs=1e-5)
    assert profile.ground_m[-1] == pytest.approx(float(heights[305, 410]), abs=1e-5)


def test_samples_between_cells_are_bilinear(tmp_path: Path) -> None:
    heights = np.zeros((20, 300), dtype=np.float32)
    heights[:, 100:] = 10.0
    dem = write_dem(tmp_path / "step.tif", heights)
    tx = centre_xy_m(10, 0)
    rx = (tx[0] + 200.0, tx[1])

    profile = sample_at(dem, tx, rx, step_m=0.25)

    by_distance = dict(zip(profile.distance_m.round(2), profile.ground_m, strict=True))
    assert by_distance[99.0] == pytest.approx(0.0)
    assert by_distance[99.25] == pytest.approx(2.5)
    assert by_distance[99.5] == pytest.approx(5.0)
    assert by_distance[100.0] == pytest.approx(10.0)
    assert profile.ground_m.min() >= 0.0
    assert profile.ground_m.max() <= 10.0


def test_profile_is_uncurved_ground(tmp_path: Path) -> None:
    dem = write_dem(
        tmp_path / "flat10.tif", np.full((10, 4100), 150.0, dtype=np.float32), resolution_m=10.0
    )

    profile = sample_at(dem, centre_xy_m(5, 20, 10.0), centre_xy_m(5, 4020, 10.0), step_m=10.0)

    assert profile.distance_m[-1] == pytest.approx(40_000.0)
    assert np.all(profile.ground_m == 150.0)
    assert profile.clutter_m is None


def test_profile_leaving_the_raster_raises_out_of_extent(tmp_path: Path) -> None:
    dem = flat_dem(tmp_path)
    tx = centre_xy_m(25, 10)
    rx = centre_xy_m(25, 1300)

    with pytest.raises(OutOfExtentError):
        sample_at(dem, tx, rx, step_m=1.0)


def test_point_in_the_outer_half_pixel_is_inside_the_extent(tmp_path: Path) -> None:
    dem = flat_dem(tmp_path, rows=10, cols=100)
    tx = centre_xy_m(5, 0)
    edge = (tx[0] - 0.5, tx[1])

    profile = sample_at(dem, edge, centre_xy_m(5, 50), step_m=1.0)

    assert np.all(profile.ground_m == 100.0)


def test_far_edge_of_the_outer_half_pixel_is_inside_the_extent(tmp_path: Path) -> None:
    dem = flat_dem(tmp_path, rows=10, cols=100)
    last = centre_xy_m(9, 99)
    corner = (last[0] + 0.5, last[1] - 0.5)

    profile = sample_at(dem, centre_xy_m(0, 40), corner, step_m=1.0)

    assert profile.ground_m[-1] == 100.0


@pytest.mark.parametrize(("dx_m", "dy_m"), [(-1e-6, 0.0), (0.0, 1e-6)])
def test_a_point_just_beyond_the_raster_edge_is_out_of_extent(
    tmp_path: Path, dx_m: float, dy_m: float
) -> None:
    dem = flat_dem(tmp_path, rows=10, cols=100)
    first = centre_xy_m(0, 0)
    beyond = (first[0] - 0.5 + dx_m, first[1] + 0.5 + dy_m)

    with pytest.raises(OutOfExtentError):
        sample_at(dem, beyond, centre_xy_m(5, 50), step_m=1.0)


def test_nodata_becomes_nan(tmp_path: Path) -> None:
    heights = np.full((20, 300), 140.0, dtype=np.float32)
    heights[10, 150] = -999999.0
    dem = write_dem(tmp_path / "gap.tif", heights, nodata=-999999.0)

    profile = sample_at(dem, centre_xy_m(10, 0), centre_xy_m(10, 299), step_m=1.0)

    assert np.isnan(profile.ground_m[150])
    assert np.isnan(profile.ground_m).sum() <= 3
    finite = profile.ground_m[np.isfinite(profile.ground_m)]
    assert np.all(finite == 140.0)


def test_long_path_is_read_in_bounded_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rng = np.random.default_rng(11)
    heights = rng.uniform(100.0, 200.0, size=(1600, 1600)).astype(np.float32)
    dem = write_dem(tmp_path / "big.tif", heights)
    tx, rx = centre_xy_m(20, 30), centre_xy_m(1570, 1500)
    whole = sample_at(dem, tx, rx, step_m=1.0, max_window_px=4000)

    shapes: list[tuple[int, int]] = []
    original_read = rasterio.io.DatasetReader.read

    def spy(self: Any, *args: Any, **kwargs: Any) -> Any:
        window = kwargs.get("window")
        assert window is not None, "a profile read without a window reads the whole raster"
        shapes.append((int(window.height), int(window.width)))
        return original_read(self, *args, **kwargs)

    monkeypatch.setattr(rasterio.io.DatasetReader, "read", spy)
    segmented = sample_at(dem, tx, rx, step_m=1.0, max_window_px=300)

    assert len(shapes) > 1
    assert all(height <= 300 and width <= 300 for height, width in shapes)
    assert np.array_equal(segmented.ground_m, whole.ground_m)


def test_zero_length_path_is_a_value_error(tmp_path: Path) -> None:
    dem = flat_dem(tmp_path)
    point = centre_xy_m(25, 10)

    with pytest.raises(ValueError, match="zero-length"):
        sample_at(dem, point, point, step_m=1.0)


def test_step_must_be_positive(tmp_path: Path) -> None:
    dem = flat_dem(tmp_path)

    with pytest.raises(ValueError, match="step"):
        sample_at(dem, centre_xy_m(25, 10), centre_xy_m(25, 20), step_m=0.0)


def test_raster_outside_utm_15n_is_refused(tmp_path: Path) -> None:
    dem = write_dem(tmp_path / "wgs84.tif", np.zeros((10, 10), dtype=np.float32), crs="EPSG:4326")

    with pytest.raises(ValueError, match="EPSG:26915"):
        sample_at(dem, centre_xy_m(5, 1), centre_xy_m(5, 8), step_m=1.0)


def test_path_profile_rejects_mismatched_arrays() -> None:
    with pytest.raises(ValueError):
        PathProfile(
            distance_m=np.array([0.0, 1.0, 2.0]),
            ground_m=np.array([1.0, 1.0]),
            tx_xy_m=(0.0, 0.0),
            rx_xy_m=(2.0, 0.0),
        )


def test_path_profile_rejects_distances_not_starting_at_zero() -> None:
    with pytest.raises(ValueError):
        PathProfile(
            distance_m=np.array([1.0, 2.0, 3.0]),
            ground_m=np.array([1.0, 1.0, 1.0]),
            tx_xy_m=(0.0, 0.0),
            rx_xy_m=(2.0, 0.0),
        )
