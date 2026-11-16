"""The x72y429 preparation command end to end, on made-up layers and snapshots.

`python -m engine.prepare x72y429 --folder <folder>` reads the two real point
layers ingest-eng writes into the folder, adds the receiver grid, checks every
box against the DEM's extent, runs the crossing check and writes the run's
point layers, `crossing.md` and `x72y429.yaml`. Every position, pseudonym and
height here is made up; the snapshots carry the real ids only so the command
finds them. The real nodes' coordinates must appear in no output but the two
point layers.
"""

from collections.abc import Iterator
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import yaml
from engine_fixture import no_logger_caching  # noqa: F401
from prepare_snapshots import write_raster_snapshot
from shapely.geometry import Point

from engine.config import load_config
from engine.grid import run_grid
from engine.points import read_points
from engine.prepare.command import main
from engine.terrain_build import check_sources, dem_resolution_m

DEM_ID = "3dep-1m-mo-saintlouis-2017-x72y429"
DSM_ID = "ept-mo-stlouis-2017-x72y429"
SITE_A = "a1" * 32
SITE_B = "b2" * 32
SITE_C = "c3" * 32
RX = "d4" * 32
SITES = [
    (SITE_A, 723_456.7, 4_284_321.9, 182.4, "both_ends_gateway"),
    (SITE_B, 725_012.3, 4_287_034.5, 91.2, "busy_pair_gateway"),
    (SITE_C, 720_100.0, 4_285_000.0, 182.4, "busy_pair_gateway"),
]
RX_ROW = (RX, 727_654.3, 4_282_345.6, 182.4)
REAL_COORDINATE_TEXT = ["723456", "4284321", "725012", "4287034", "720100", "727654", "4282345"]


@pytest.fixture(scope="module")
def snapshots_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A flat 10 m DEM over the tile and a DSM with one tall cell on the A to RX path."""
    root = tmp_path_factory.mktemp("snapshots")
    dem = np.full((1000, 1000), 150.0, dtype=np.float32)
    dsm = dem + 5.0
    # Cell 725550 to 725560 E, 4283330 to 4283340 N holds the path's midpoint.
    dsm[666, 555] = 350.0
    corner = (720_000.0, 4_290_000.0)
    write_raster_snapshot(root, DEM_ID, {"dem.tif": (dem, 10.0, corner)})
    write_raster_snapshot(root, DSM_ID, {"dsm.tif": (dsm, 10.0, corner)})
    return root


@pytest.fixture
def env(snapshots_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SNAPSHOTS_DIR", str(snapshots_dir))
    monkeypatch.setenv("LOG_FORMAT", "console")
    yield


def write_inputs(folder: Path, sites: list[tuple] = SITES, receiver: tuple = RX_ROW) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    site_layer = gpd.GeoDataFrame(
        {
            "site_id": [s[0] for s in sites],
            "box_half_width_m": [s[3] for s in sites],
            "role": [s[4] for s in sites],
        },
        geometry=[Point(s[1], s[2]) for s in sites],
        crs="EPSG:26915",
    )
    site_layer.to_parquet(folder / "tx_sites_real.parquet")
    rx_layer = gpd.GeoDataFrame(
        {"rx_id": [receiver[0]], "box_half_width_m": [receiver[3]]},
        geometry=[Point(receiver[1], receiver[2])],
        crs="EPSG:26915",
    )
    rx_layer.to_parquet(folder / "rx_real.parquet")
    return folder


def run(folder: Path, tile: str = "x72y429") -> int:
    return main([tile, "--folder", str(folder)])


def assert_no_real_coordinates(text: str) -> None:
    for digits in REAL_COORDINATE_TEXT:
        assert digits not in text


@pytest.mark.usefixtures("env")
def test_the_command_writes_the_run_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    assert run(folder) == 0
    names = sorted(p.name for p in folder.iterdir())
    assert names == [
        "crossing.md",
        "rx_points.parquet",
        "rx_real.parquet",
        "tx_sites.parquet",
        "tx_sites_real.parquet",
        "x72y429.yaml",
    ]
    out, err = capsys.readouterr()
    assert out.strip() == (folder / "crossing.md").read_text(encoding="utf-8").strip()
    assert_no_real_coordinates(out + err)


@pytest.mark.usefixtures("env")
def test_rx_points_are_the_real_receiver_then_the_grid(tmp_path: Path) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    assert run(folder) == 0
    layer = gpd.read_parquet(folder / "rx_points.parquet")
    assert list(layer.columns) == ["rx_id", "geometry", "box_half_width_m"]
    assert layer.crs.to_epsg() == 26915
    assert len(layer) == 401
    assert layer["rx_id"].iloc[0] == RX
    assert (layer.geometry.iloc[0].x, layer.geometry.iloc[0].y) == (727_654.3, 4_282_345.6)
    assert layer["box_half_width_m"].iloc[0] == 182.4
    assert layer["rx_id"].iloc[1] == "grid-r00c00"
    assert (layer["box_half_width_m"].iloc[1:] == 0.0).all()
    assert layer["box_half_width_m"].dtype == np.float64
    assert len(read_points(folder / "rx_points.parquet", "rx_id")) == 401


@pytest.mark.usefixtures("env")
def test_tx_sites_hold_only_the_sites_inside_the_extent(tmp_path: Path) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    assert run(folder) == 0
    layer = gpd.read_parquet(folder / "tx_sites.parquet")
    assert list(layer.columns) == ["site_id", "geometry", "box_half_width_m"]
    assert list(layer["site_id"]) == [SITE_A, SITE_B]
    assert [(p.x, p.y) for p in layer.geometry] == [
        (723_456.7, 4_284_321.9),
        (725_012.3, 4_287_034.5),
    ]
    assert list(layer["box_half_width_m"]) == [182.4, 91.2]


@pytest.mark.usefixtures("env")
def test_the_written_layers_make_a_run_extent_the_engine_accepts(
    tmp_path: Path, snapshots_dir: Path
) -> None:
    from prepare_snapshots import named

    folder = write_inputs(tmp_path / "x72y429")
    assert run(folder) == 0
    endpoints = read_points(folder / "tx_sites.parquet", "site_id") + read_points(
        folder / "rx_points.parquet", "rx_id"
    )
    dem = named(snapshots_dir, DEM_ID)["dem"]
    check_sources({"dem": dem}, run_grid(endpoints, dem_resolution_m(dem)))


@pytest.mark.usefixtures("env")
def test_the_config_names_the_run_the_interface_needs(tmp_path: Path) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    assert run(folder) == 0
    path = folder / "x72y429.yaml"
    config = load_config(path)
    assert config.run_id == "x72y429"
    assert config.frequency_mhz == 906.875
    assert config.models == ("fspl", "knife_edge", "hata", "itm", "p1812")
    assert config.tx_profile.name == config.rx_profile.name == "mesh_node"
    assert config.tx_sites == folder / "tx_sites.parquet"
    assert config.rx_points == folder / "rx_points.parquet"
    assert config.snapshots.named() == {
        "dem": DEM_ID,
        "dsm": DSM_ID,
        "canopy": "canopy-v2-0231113110",
        "buildings": "osm-geofabrik-mo-il-2026-11-01",
        "p1812_maps": "itu-r-p1812-8-202509",
    }
    assert config.endpoint_draws == 16
    assert config.seed == 20261108
    assert config.calibration is None
    assert config.output_dir == Path("/runs")
    text = path.read_text(encoding="utf-8")
    assert "\noutput_dir: /runs\n" in text
    assert_no_real_coordinates(text)


@pytest.mark.usefixtures("env")
def test_crossing_md_lists_real_pairs_by_pseudonym_and_grid_paths_as_counts(
    tmp_path: Path,
) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    assert run(folder) == 0
    text = (folder / "crossing.md").read_text(encoding="utf-8")
    assert f"| {SITE_A} | both_ends_gateway | {RX} | yes | 0.0 |" in text
    assert f"| {SITE_B} | busy_pair_gateway | {RX} | no |" in text
    assert f"| {SITE_C} | busy_pair_gateway | left out |" in text
    assert "| 1 |" in text  # one tall cell
    for site in (SITE_A, SITE_B):
        assert f"| {site} | 400 |" in text
    assert "never published" in text
    assert_no_real_coordinates(text)


@pytest.mark.usefixtures("env")
def test_a_real_receiver_outside_the_extent_stops_the_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = write_inputs(tmp_path / "x72y429", receiver=(RX, 720_030.0, 4_282_345.6, 182.4))
    assert run(folder) == 2
    assert sorted(p.name for p in folder.iterdir()) == ["rx_real.parquet", "tx_sites_real.parquet"]
    _, err = capsys.readouterr()
    assert RX in err
    assert "extent" in err
    assert "720030" not in err
    assert_no_real_coordinates(err)


@pytest.mark.usefixtures("env")
def test_no_site_inside_the_extent_stops_the_command(tmp_path: Path) -> None:
    folder = write_inputs(tmp_path / "x72y429", sites=[SITES[2]])
    assert run(folder) == 2
    assert not (folder / "x72y429.yaml").exists()


@pytest.mark.usefixtures("env")
def test_an_existing_output_is_never_overwritten(tmp_path: Path) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    (folder / "crossing.md").write_text("earlier\n", encoding="utf-8")
    assert run(folder) == 2
    assert (folder / "crossing.md").read_text(encoding="utf-8") == "earlier\n"
    assert not (folder / "x72y429.yaml").exists()


@pytest.mark.usefixtures("env")
def test_a_folder_inside_a_git_checkout_is_refused(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    folder = write_inputs(tmp_path / "configs" / "x72y429")
    assert run(folder) == 2
    assert not (folder / "x72y429.yaml").exists()


@pytest.mark.usefixtures("env")
def test_a_missing_input_layer_is_refused(tmp_path: Path) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    (folder / "rx_real.parquet").unlink()
    assert run(folder) == 2


@pytest.mark.usefixtures("env")
@pytest.mark.parametrize("drop", ["box_half_width_m", "role"])
def test_a_site_layer_without_an_agreed_column_is_refused(tmp_path: Path, drop: str) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    layer = gpd.read_parquet(folder / "tx_sites_real.parquet").drop(columns=[drop])
    layer.to_parquet(folder / "tx_sites_real.parquet")
    assert run(folder) == 2


@pytest.mark.usefixtures("env")
def test_an_unknown_site_role_is_refused(tmp_path: Path) -> None:
    sites = [(*SITES[0][:4], "gateway")]
    folder = write_inputs(tmp_path / "x72y429", sites=sites)
    assert run(folder) == 2


@pytest.mark.usefixtures("env")
def test_a_tile_without_a_defined_run_is_refused(tmp_path: Path) -> None:
    folder = write_inputs(tmp_path / "x71y428")
    assert run(folder, tile="x71y428") == 2


def test_an_unset_snapshots_dir_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SNAPSHOTS_DIR", raising=False)
    folder = write_inputs(tmp_path / "x72y429")
    assert run(folder) == 2


def test_the_yaml_holds_only_interface_fields(tmp_path: Path, env: None) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    assert run(folder) == 0
    fields = yaml.safe_load((folder / "x72y429.yaml").read_text(encoding="utf-8"))
    assert list(fields) == [
        "run_id",
        "frequency_mhz",
        "models",
        "tx_profile",
        "rx_profile",
        "tx_sites",
        "rx_points",
        "snapshots",
        "endpoint_draws",
        "seed",
        "calibration",
        "output_dir",
    ]
