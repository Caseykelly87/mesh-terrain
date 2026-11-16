"""The engine's parts on hand-made inputs: hashes, grid, coverage, calibration, buildings, draws.

Coordinates are EPSG:26915 metres near St. Louis. Expected values are worked
by hand in each test.
"""

import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
from engine_fixture import no_logger_caching
from rasterio.transform import from_origin
from shapely.geometry import Point, box

from engine.buildings import Footprints
from engine.calibration import calibration_table, model_fits
from engine.coverage import coverage_values
from engine.grid import RunGrid, run_grid
from engine.hashing import raster_content_sha256, table_content_sha256
from engine.points import Endpoint
from engine.predict import draws_for
from pipeline.common.snapshot import sha256_file
from terrain.draws import EndpointBox


def endpoint(name: str, x_m: float, y_m: float, half_width_m: float = 0.0) -> Endpoint:
    return Endpoint(
        endpoint_id=name, box=EndpointBox(centre_xy_m=(x_m, y_m), half_width_m=half_width_m)
    )


def test_table_content_hash_ignores_the_file_and_sees_the_rows(tmp_path: Path) -> None:
    frame = gpd.GeoDataFrame(
        {"rx_id": ["a", "b"], "loss_db": [101.5, None]},
        geometry=[Point(725_000, 4_285_000), Point(725_010, 4_285_000)],
        crs="EPSG:26915",
    )
    frame.to_parquet(tmp_path / "snappy.parquet", index=False, compression="snappy")
    frame.to_parquet(tmp_path / "zstd.parquet", index=False, compression="zstd")
    frame.assign(loss_db=[101.5, 0.0]).to_parquet(tmp_path / "other.parquet", index=False)
    assert sha256_file(tmp_path / "snappy.parquet") != sha256_file(tmp_path / "zstd.parquet")
    assert table_content_sha256(tmp_path / "snappy.parquet") == table_content_sha256(
        tmp_path / "zstd.parquet"
    )
    assert table_content_sha256(tmp_path / "other.parquet") != table_content_sha256(
        tmp_path / "zstd.parquet"
    )


def write_cells(path: Path, cells: np.ndarray, compress: str) -> None:
    profile = {
        "driver": "GTiff",
        "height": cells.shape[0],
        "width": cells.shape[1],
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:26915",
        "nodata": -9999.0,
        "compress": compress,
        "transform": from_origin(725_000.0, 4_286_000.0, 10.0, 10.0),
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(cells.astype("float32"), 1)


def test_raster_content_hash_ignores_compression_and_sees_the_cells(tmp_path: Path) -> None:
    cells = np.arange(300 * 7, dtype="float32").reshape(300, 7)
    write_cells(tmp_path / "deflate.tif", cells, "deflate")
    write_cells(tmp_path / "lzw.tif", cells, "lzw")
    cells[299, 6] = 0.5
    write_cells(tmp_path / "other.tif", cells, "lzw")
    deflate = raster_content_sha256(tmp_path / "deflate.tif")
    assert deflate == raster_content_sha256(tmp_path / "lzw.tif")
    assert deflate != raster_content_sha256(tmp_path / "other.tif")


def test_run_grid_covers_every_box_plus_the_margin_on_10_m_edges() -> None:
    grid = run_grid(
        [endpoint("a", 725_103.2, 4_285_207.9, 20.0), endpoint("b", 725_901.0, 4_285_888.8)], 1.0
    )
    assert (grid.west_m, grid.south_m) == (725_030.0, 4_285_130.0)
    assert (grid.east_m, grid.north_m) == (725_960.0, 4_285_940.0)
    assert (grid.width, grid.height) == (930, 810)
    assert grid.at_resolution(10.0).shape == (81, 93)
    assert grid.transform == from_origin(725_030.0, 4_285_940.0, 1.0, 1.0)


def test_coverage_interpolates_a_plane_exactly_inside_the_receivers() -> None:
    grid = RunGrid(
        west_m=725_000.0,
        south_m=4_285_000.0,
        east_m=725_100.0,
        north_m=4_285_100.0,
        resolution_m=10.0,
    )
    xy = np.array(
        [
            [725_000.0, 4_285_000.0],
            [725_100.0, 4_285_000.0],
            [725_000.0, 4_285_100.0],
            [725_060.0, 4_285_040.0],
        ]
    )
    plane = 100.0 + 0.1 * (xy[:, 0] - 725_000.0) + 0.02 * (xy[:, 1] - 4_285_000.0)
    cells = coverage_values(xy, plane, grid)
    assert cells.shape == (10, 10)
    # Cell centre (725 005, 4 285 005) is the bottom-left cell: row 9, column 0.
    assert cells[9, 0] == pytest.approx(100.0 + 0.5 + 0.1)
    assert np.isnan(cells[0, 9]), "the north-east corner lies outside the receivers' hull"


def test_coverage_with_too_few_receivers_is_all_nodata() -> None:
    grid = RunGrid(
        west_m=725_000.0,
        south_m=4_285_000.0,
        east_m=725_100.0,
        north_m=4_285_100.0,
        resolution_m=10.0,
    )
    xy = np.array([[725_000.0, 4_285_000.0], [725_100.0, 4_285_100.0]])
    assert np.isnan(coverage_values(xy, np.array([1.0, 2.0]), grid)).all()
    collinear = np.array(
        [[725_000.0, 4_285_000.0], [725_050.0, 4_285_050.0], [725_100.0, 4_285_100.0]]
    )
    assert np.isnan(coverage_values(collinear, np.array([1.0, 2.0, 3.0]), grid)).all()


def test_calibration_residuals_and_fits_by_hand() -> None:
    observations = pd.DataFrame(
        {
            "site_id": ["s", "s", "s", "s"],
            "rx_id": ["a", "b", "c", "z"],
            "observed_loss_db": [120.0, 130.0, 110.0, 99.0],
        }
    )
    predictions = pd.DataFrame(
        {
            "site_id": ["s", "s", "s"],
            "rx_id": ["a", "b", "c"],
            "fspl_db_median": [118.0, 133.0, None],
            "hata_db_median": [120.0, 130.0, 110.0],
        }
    )
    table = calibration_table(observations, predictions, ("fspl", "hata"), "run-1")
    assert list(table["fspl_residual_db"].iloc[:2]) == [2.0, -3.0]
    assert table["fspl_residual_db"].iloc[2:].isna().all()
    assert set(table["run_id"]) == {"run-1"}
    fits = {fit.model: fit for fit in model_fits(table, ("fspl", "hata"))}
    assert fits["fspl"].n == 2
    assert fits["fspl"].rmse_db == pytest.approx(math.sqrt((4 + 9) / 2))
    assert fits["fspl"].mae_db == pytest.approx(2.5)
    assert fits["fspl"].bias_db == pytest.approx(-0.5)
    assert fits["hata"].n == 3 and fits["hata"].rmse_db == 0.0


def footprints(*shapes: object) -> Footprints:
    layer = gpd.GeoDataFrame(
        {"osm_id": range(len(shapes))}, geometry=list(shapes), crs="EPSG:26915"
    )
    return Footprints.from_frame(layer)


def test_building_share_within_100_m() -> None:
    centre = (725_500.0, 4_285_500.0)
    assert footprints(box(725_000, 4_285_000, 726_000, 4_286_000)).share_within(
        centre
    ) == pytest.approx(1.0)
    east_half = footprints(box(725_500, 4_285_000, 726_000, 4_286_000))
    assert east_half.share_within(centre) == pytest.approx(0.5, abs=1e-3)
    overlapping = footprints(
        box(725_500, 4_285_000, 726_000, 4_286_000), box(725_500, 4_285_400, 725_700, 4_285_600)
    )
    assert overlapping.share_within(centre) == pytest.approx(0.5, abs=1e-3)
    assert footprints(box(726_000, 4_286_000, 726_010, 4_286_010)).share_within(centre) == 0.0


def test_building_footprints_are_read_in_any_crs_and_reprojected() -> None:
    square = gpd.GeoDataFrame(
        {"osm_id": [1]}, geometry=[box(725_490, 4_285_490, 725_510, 4_285_510)], crs="EPSG:26915"
    ).to_crs("EPSG:4326")
    share = Footprints.from_frame(square).share_within((725_500.0, 4_285_500.0))
    assert share == pytest.approx(400.0 / (math.pi * 100.0**2), rel=2e-3)


@pytest.mark.parametrize(
    ("tx_half_m", "rx_half_m", "expected"), [(0.0, 0.0, 1), (0.0, 5.0, 16), (5.0, 0.0, 16)]
)
def test_points_take_one_draw_and_boxes_take_the_configured_number(
    tx_half_m: float, rx_half_m: float, expected: int
) -> None:
    tx = EndpointBox(centre_xy_m=(725_000.0, 4_285_000.0), half_width_m=tx_half_m)
    rx = EndpointBox(centre_xy_m=(725_500.0, 4_285_000.0), half_width_m=rx_half_m)
    assert draws_for(tx, rx, 16) == expected


# The import above activates the autouse fixture for this module.
_ = no_logger_caching
