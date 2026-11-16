"""The engine's parts on hand-made inputs: hashes, grid, coverage, calibration, buildings, draws.

Coordinates are EPSG:26915 metres near St. Louis. Expected values are worked
by hand in each test.
"""

import math
from pathlib import Path
from typing import get_args

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
from engine.errors import ConfigError
from engine.grid import RunGrid, run_grid
from engine.hashing import raster_content_sha256, table_content_sha256
from engine.points import Endpoint
from engine.predict import PairPrediction, draws_for
from engine.predictions import summary_value
from engine.profiles import load_profile
from engine.provenance import py1812_pin
from engine.quantities import LinkQuantities
from pipeline.common.snapshot import sha256_file
from terrain.draws import DrawProfiles, DrawSummary, EndpointBox
from terrain.errors import RefusalReason, RefusedDrawError
from terrain.path_loss import DrawModels
from terrain.profile import PathProfile


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


# Review of PR #40: S1, S2 (coverage, bearing, clutter), S4 and the hashing and Py1812 nits.


def test_coverage_at_a_receiver_on_a_cell_centre_is_that_receivers_value() -> None:
    grid = RunGrid(
        west_m=725_000.0,
        south_m=4_285_000.0,
        east_m=725_100.0,
        north_m=4_285_100.0,
        resolution_m=10.0,
    )
    xy = np.array(
        [
            [725_005.0, 4_285_095.0],
            [725_095.0, 4_285_095.0],
            [725_005.0, 4_285_005.0],
            [725_095.0, 4_285_005.0],
            [725_055.0, 4_285_045.0],
        ]
    )
    values = np.array([101.0, 140.0, 97.0, 123.0, 160.0])
    cells = coverage_values(xy, values, grid)
    assert cells[0, 0] == pytest.approx(101.0)
    assert cells[0, 9] == pytest.approx(140.0)
    assert cells[9, 0] == pytest.approx(97.0)
    assert cells[9, 9] == pytest.approx(123.0)
    assert cells[5, 5] == pytest.approx(160.0)


def pair(tx_xy_m: tuple[float, float], rx_xy_m: tuple[float, float]) -> PairPrediction:
    return PairPrediction(
        site_id="s",
        rx_id="r",
        tx_xy_m=tx_xy_m,
        rx_xy_m=rx_xy_m,
        endpoint_draws=1,
        seed=0,
        summary=None,
        hata_variant=None,
        mean_building_share=None,
    )


@pytest.mark.parametrize(
    ("offset", "expected"),
    [((100.0, 0.0), 90.0), ((0.0, 100.0), 0.0), ((0.0, -100.0), 180.0), ((-100.0, 0.0), 270.0)],
)
def test_bearing_is_clockwise_from_grid_north(offset: tuple[float, float], expected: float) -> None:
    tx = (725_000.0, 4_285_000.0)
    rx = (tx[0] + offset[0], tx[1] + offset[1])
    assert pair(tx, rx).bearing_deg == pytest.approx(expected)


def flat_draw(clutter_m: list[float]) -> DrawProfiles:
    ground = PathProfile(
        distance_m=np.linspace(0.0, 100.0, 101),
        ground_m=np.full(101, 150.0),
        tx_xy_m=(725_000.0, 4_285_000.0),
        rx_xy_m=(725_100.0, 4_285_000.0),
    )
    cells = PathProfile(
        distance_m=np.linspace(0.0, 100.0, len(clutter_m)),
        ground_m=np.full(len(clutter_m), 150.0),
        tx_xy_m=ground.tx_xy_m,
        rx_xy_m=ground.rx_xy_m,
        clutter_m=np.array(clutter_m),
    )
    return DrawProfiles(ground=ground, p1812=cells, dsm_filled_pixels=0)


def fspl_only() -> DrawModels:
    node = load_profile("mesh_node")
    return DrawModels(models=("fspl",), frequency_mhz=906.875, tx=node, rx=node)


def test_the_clutter_of_a_draw_is_the_median_over_every_interior_point() -> None:
    quantities = LinkQuantities(models=fspl_only(), cells_required=False)
    quantities(flat_draw([0.0, 1.0, 2.0, 9.0, 0.0]))
    quantities(flat_draw([0.0, 4.0, 6.0, 0.0]))
    assert quantities.clutter_m == [2.0, 5.0]


def test_a_draw_without_cell_interior_has_no_clutter_unless_p1812_needs_the_cells() -> None:
    lenient = LinkQuantities(models=fspl_only(), cells_required=False)
    assert "fspl_db" in lenient(flat_draw([0.0, 0.0])).values
    assert lenient.clutter_m == [None]
    strict = LinkQuantities(models=fspl_only(), cells_required=True)
    with pytest.raises(RefusedDrawError) as refused:
        strict(flat_draw([0.0, 0.0]))
    assert refused.value.reason == "short_profile"
    assert strict.clutter_m == []


def kept_summary(medians: dict[str, float]) -> DrawSummary:
    return DrawSummary(
        link_id="x",
        seed=1,
        endpoint_draws=1,
        refusals=dict.fromkeys(get_args(RefusalReason), 0),
        medians=medians,
        iqrs=medians,
        shares={},
        tx_building_draws=None,
        rx_building_draws=None,
        dsm_filled_pixels_median=None,
    )


def test_a_missing_quantity_is_null_only_where_the_run_allows_it() -> None:
    summary = kept_summary({"fspl_db": 100.0})
    assert summary_value(summary, "median", "fspl_db", frozenset()) == 100.0
    assert summary_value(summary, "median", "p1812_db", frozenset({"p1812_db"})) is None
    with pytest.raises(KeyError):
        summary_value(summary, "median", "p1812_db", frozenset())
    assert summary_value(None, "median", "fspl_db", frozenset()) is None


def test_footprints_outside_the_window_are_left_out() -> None:
    near = box(725_490, 4_285_490, 725_510, 4_285_510)
    far = box(745_000, 4_285_000, 745_010, 4_285_010)
    layer = gpd.GeoDataFrame({"osm_id": [1, 2]}, geometry=[near, far], crs="EPSG:26915")
    kept = Footprints.from_frame(layer, window_m=(725_000.0, 4_285_000.0, 726_000.0, 4_286_000.0))
    assert len(kept.parts) == 1 and kept.parts[0].equals(near)


def test_table_content_hash_sees_the_geoparquet_crs(tmp_path: Path) -> None:
    frame = gpd.GeoDataFrame(
        {"rx_id": ["a"]}, geometry=[Point(725_000, 4_285_000)], crs="EPSG:26915"
    )
    frame.to_parquet(tmp_path / "utm.parquet", index=False)
    frame.set_crs("EPSG:32615", allow_override=True).to_parquet(
        tmp_path / "wgs.parquet", index=False
    )
    assert table_content_sha256(tmp_path / "utm.parquet") != table_content_sha256(
        tmp_path / "wgs.parquet"
    )


def test_py1812_archive_hash_is_checked_against_the_installed_metadata() -> None:
    lock = Path(__file__).resolve().parents[1] / "uv.lock"
    commit, archive_sha256 = py1812_pin(lock)
    url = f"https://github.com/eeveetza/Py1812/archive/{commit}.tar.gz"
    wrong = {"url": url, "archive_info": {"hashes": {"sha256": "0" * 64}}}
    with pytest.raises(ConfigError, match="archive"):
        py1812_pin(lock, direct_url=wrong)
    right = {"url": url, "archive_info": {"hashes": {"sha256": archive_sha256}}}
    assert py1812_pin(lock, direct_url=right) == (commit, archive_sha256)


def test_py1812_missing_from_the_environment_is_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    from importlib import metadata

    def not_installed(name: str) -> None:
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "distribution", not_installed)
    with pytest.raises(ConfigError, match="Py1812 is not installed"):
        py1812_pin(Path(__file__).resolve().parents[1] / "uv.lock")


# The import above activates the autouse fixture for this module.
_ = no_logger_caching
