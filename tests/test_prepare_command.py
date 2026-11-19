"""The x72y429 preparation command end to end, on made-up layers and snapshots.

`python -m engine.prepare x72y429 --folder <folder>` reads the two real point
layers ingest-eng writes into the folder, adds the receiver grid, checks every
box against the DEM's extent, runs the crossing check and writes the run's
point layers, `crossing.md` and `x72y429.yaml`. Every position, pseudonym and
height here is made up; the snapshots carry the real ids only so the command
finds them. The real nodes' coordinates must appear in no output but the two
point layers.
"""

from collections.abc import Callable, Iterator
from pathlib import Path

import geopandas as gpd
import numpy as np
import pyarrow.parquet as pq
import pytest
import yaml
from engine_fixture import no_logger_caching  # noqa: F401
from prepare_snapshots import named, write_raster_snapshot
from shapely.geometry import Point
from structlog.testing import capture_logs

import engine.prepare.command as command
from engine.config import load_config
from engine.grid import run_grid
from engine.points import read_points
from engine.positions_marker import positions_marker, stamp_positions
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
    (SITE_B, 725_012.3, 4_287_034.5, 91.2, "gateway_only_gateway"),
    (SITE_C, 720_100.0, 4_285_000.0, 182.4, "gateway_only_gateway"),
]
RX_ROW = (RX, 727_654.3, 4_282_345.6, 182.4)
NEVER_PUBLISHED = "inherit the rule that precise MeshSTL positions are never published"
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


@pytest.fixture(autouse=True)
def keep_log_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    # configure_logging caches loggers process-wide, which would hide later
    # tests' log lines from structlog's capture_logs.
    monkeypatch.setattr(command, "configure_logging", lambda log_format: None)


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


def refusal(folder: Path, tile: str = "x72y429") -> str:
    """Run the command, expecting a refusal; return the logged reason."""
    with capture_logs() as logs:
        assert run(folder, tile) == 2
    (refused,) = [entry for entry in logs if entry["log_level"] == "error"]
    reason = str(refused["reason"])
    assert_no_real_coordinates(str(logs))
    return reason


@pytest.mark.usefixtures("env")
def test_the_command_writes_the_run_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    with capture_logs() as logs:
        assert run(folder) == 0
    assert_no_real_coordinates(str(logs))
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
    # Casey, issue #44, decision 2 (a): mesh_node at both ends for this run.
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
    assert f"| {SITE_B} | gateway_only_gateway | {RX} | no |" in text
    assert f"| {SITE_C} | gateway_only_gateway | left out |" in text
    assert "| 1 |" in text  # one tall cell
    for site in (SITE_A, SITE_B):
        assert f"| {site} | 400 |" in text
    assert NEVER_PUBLISHED in text
    assert "read locally only and never posted" in text
    assert "## Paths between real endpoints" in text
    assert_no_real_coordinates(text)


@pytest.mark.usefixtures("env")
def test_a_real_receiver_outside_the_extent_stops_the_command(tmp_path: Path) -> None:
    folder = write_inputs(tmp_path / "x72y429", receiver=(RX, 720_030.0, 4_282_345.6, 182.4))
    reason = refusal(folder)
    assert sorted(p.name for p in folder.iterdir()) == ["rx_real.parquet", "tx_sites_real.parquet"]
    assert RX in reason
    assert "extent" in reason
    assert "720030" not in reason


@pytest.mark.usefixtures("env")
def test_no_site_inside_the_extent_stops_the_command(tmp_path: Path) -> None:
    folder = write_inputs(tmp_path / "x72y429", sites=[SITES[2]])
    assert "no site" in refusal(folder)
    assert not (folder / "x72y429.yaml").exists()


@pytest.mark.usefixtures("env")
def test_an_existing_output_is_never_overwritten(tmp_path: Path) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    (folder / "crossing.md").write_text("earlier\n", encoding="utf-8")
    assert "never overwrites" in refusal(folder)
    assert (folder / "crossing.md").read_text(encoding="utf-8") == "earlier\n"
    assert not (folder / "x72y429.yaml").exists()


@pytest.mark.usefixtures("env")
def test_a_folder_inside_a_git_checkout_is_refused(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    folder = write_inputs(tmp_path / "configs" / "x72y429")
    assert "outside git" in refusal(folder)
    assert not (folder / "x72y429.yaml").exists()


@pytest.mark.usefixtures("env")
def test_a_missing_input_layer_is_refused(tmp_path: Path) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    (folder / "rx_real.parquet").unlink()
    assert "rx_real.parquet" in refusal(folder)


@pytest.mark.usefixtures("env")
@pytest.mark.parametrize("drop", ["box_half_width_m", "role"])
def test_a_site_layer_without_an_agreed_column_is_refused(tmp_path: Path, drop: str) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    layer = gpd.read_parquet(folder / "tx_sites_real.parquet").drop(columns=[drop])
    layer.to_parquet(folder / "tx_sites_real.parquet")
    assert drop in refusal(folder)


@pytest.mark.usefixtures("env")
def test_an_unknown_site_role_is_refused(tmp_path: Path) -> None:
    sites = [(*SITES[0][:4], "gateway")]
    folder = write_inputs(tmp_path / "x72y429", sites=sites)
    assert "'gateway'" in refusal(folder)


@pytest.mark.usefixtures("env")
def test_a_tile_without_a_defined_run_is_refused(tmp_path: Path) -> None:
    folder = write_inputs(tmp_path / "x71y428")
    assert "x71y428" in refusal(folder, tile="x71y428")


def test_an_unset_snapshots_dir_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SNAPSHOTS_DIR", raising=False)
    folder = write_inputs(tmp_path / "x72y429")
    assert "SNAPSHOTS_DIR" in refusal(folder)


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


@pytest.mark.usefixtures("env")
def test_a_site_id_that_cannot_name_a_coverage_file_is_refused(tmp_path: Path) -> None:
    sites = [("a1/" + "a1" * 31, *SITES[0][1:])]
    folder = write_inputs(tmp_path / "x72y429", sites=sites)
    assert "coverage file" in refusal(folder)


@pytest.mark.usefixtures("env")
def test_a_real_receiver_id_that_repeats_a_grid_id_is_refused(tmp_path: Path) -> None:
    folder = write_inputs(tmp_path / "x72y429", receiver=("grid-r00c00", *RX_ROW[1:]))
    assert "grid-r00c00" in refusal(folder)


@pytest.mark.usefixtures("env")
def test_a_site_left_out_is_logged_at_warning(tmp_path: Path) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    with capture_logs() as logs:
        assert run(folder) == 0
    (left_out,) = [entry for entry in logs if entry["event"] == "prepare_site_left_out"]
    assert left_out["log_level"] == "warning"
    assert left_out["site_id"] == SITE_C
    assert left_out["dem"] == DEM_ID


@pytest.mark.usefixtures("env")
def test_reads_scan_and_writes_log_begin_lines_and_the_write_its_duration(tmp_path: Path) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    with capture_logs() as logs:
        assert run(folder) == 0
    events = [entry["event"] for entry in logs]
    for begin, end in [
        ("prepare_layers_read_begin", "prepare_layers_read_end"),
        ("tall_cells_begin", "tall_cells_found"),
        ("prepare_write_begin", "prepare_written"),
    ]:
        assert events.index(begin) < events.index(end)
    (written,) = [entry for entry in logs if entry["event"] == "prepare_written"]
    assert written["duration_s"] >= 0.0


@pytest.mark.usefixtures("env")
def test_a_config_the_engine_refuses_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    monkeypatch.setattr(command, "config_text", lambda tile: "run_id: x72y429\n")
    with pytest.raises(RuntimeError, match="engine's check"):
        run(folder)
    assert sorted(p.name for p in folder.iterdir()) == ["rx_real.parquet", "tx_sites_real.parquet"]


@pytest.mark.usefixtures("env")
def test_a_failure_while_moving_outputs_into_place_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    moved: list[Path] = []
    real_replace = command.move_into_place

    def fail_on_third(source: Path, target: Path) -> None:
        if len(moved) == 2:
            raise OSError("disk full")
        real_replace(source, target)
        moved.append(target)

    monkeypatch.setattr(command, "move_into_place", fail_on_third)
    with pytest.raises(OSError, match="disk full"):
        run(folder)
    assert sorted(p.name for p in folder.iterdir()) == ["rx_real.parquet", "tx_sites_real.parquet"]


@pytest.mark.usefixtures("env")
@pytest.mark.parametrize("marker", [None, "real"])
def test_outputs_from_real_or_unmarked_layers_are_stamped_real(
    tmp_path: Path, marker: str | None
) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    if marker is not None:
        for name in ("tx_sites_real.parquet", "rx_real.parquet"):
            stamp_positions(folder / name, marker)
    assert run(folder) == 0
    assert positions_marker(folder / "rx_points.parquet") == "real"
    assert positions_marker(folder / "tx_sites.parquet") == "real"


@pytest.mark.usefixtures("env")
def test_outputs_are_synthetic_only_when_every_input_row_is(tmp_path: Path) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    stamp_positions(folder / "tx_sites_real.parquet", "synthetic")
    stamp_positions(folder / "rx_real.parquet", "real")
    assert run(folder) == 0
    assert positions_marker(folder / "tx_sites.parquet") == "synthetic"
    # The grid is made up, but the one real receiver makes the layer real.
    assert positions_marker(folder / "rx_points.parquet") == "real"


@pytest.mark.usefixtures("env")
def test_all_synthetic_inputs_give_synthetic_outputs(tmp_path: Path) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    for name in ("tx_sites_real.parquet", "rx_real.parquet"):
        stamp_positions(folder / name, "synthetic")
    assert run(folder) == 0
    assert positions_marker(folder / "rx_points.parquet") == "synthetic"
    assert positions_marker(folder / "tx_sites.parquet") == "synthetic"


@pytest.mark.usefixtures("env")
def test_an_input_marker_other_than_real_or_synthetic_is_refused(tmp_path: Path) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    path = folder / "rx_real.parquet"
    table = pq.read_table(path)
    metadata = {**table.schema.metadata, b"mesh_terrain:positions": b"maybe"}
    pq.write_table(table.replace_schema_metadata(metadata), path)
    assert "mesh_terrain:positions" in refusal(folder)


@pytest.mark.usefixtures("env")
def test_layers_written_by_ingest_engs_command_are_accepted(tmp_path: Path) -> None:
    # The interface the lead set for both agents (issue #44): ingest-eng's own
    # writer produces the folder, and this command reads it as written.
    from ingester.engine_inputs import write_layers
    from ingester.tile_selection import PlacedNode

    def placed(row: tuple, role: str) -> PlacedNode:
        return PlacedNode(
            pseudonym=row[0],
            role=role,
            easting_m=row[1],
            northing_m=row[2],
            half_width_m=row[3],
            precise_source=False,
        )

    folder = tmp_path / "x72y429"
    write_layers(folder, [placed(s, s[4]) for s in SITES], placed(RX_ROW, "both_ends_relayer"))
    assert run(folder) == 0
    assert list(gpd.read_parquet(folder / "tx_sites.parquet")["site_id"]) == [SITE_A, SITE_B]
    assert gpd.read_parquet(folder / "rx_points.parquet")["rx_id"].iloc[0] == RX
    assert positions_marker(folder / "rx_points.parquet") == "real"
    assert positions_marker(folder / "tx_sites.parquet") == "real"


def staging_warnings(logs: list[dict[str, object]]) -> list[dict[str, object]]:
    return [entry for entry in logs if entry["event"] == "prepare_staging_left"]


@pytest.mark.usefixtures("env")
def test_a_staging_folder_that_cannot_be_removed_is_logged_and_the_first_error_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    monkeypatch.setattr(command, "config_text", lambda tile: "run_id: x72y429\n")

    def rmtree_that_fails(path: Path, *, onexc: Callable[..., None]) -> None:
        onexc(Path.unlink, Path(path) / "x72y429.yaml", PermissionError("in use"))

    monkeypatch.setattr(command, "rmtree", rmtree_that_fails)
    with capture_logs() as logs, pytest.raises(RuntimeError, match="engine's check"):
        run(folder)
    (left,) = staging_warnings(logs)
    assert left["log_level"] == "warning"
    assert str(left["path"]).endswith("x72y429.yaml")
    assert ".prepare-" in str(left["path"])


@pytest.mark.usefixtures("env")
def test_an_output_that_cannot_be_removed_after_a_failure_is_logged_and_the_first_error_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    moved: list[Path] = []
    real_move = command.move_into_place

    def fail_on_third(source: Path, target: Path) -> None:
        if len(moved) == 2:
            raise OSError("disk full")
        real_move(source, target)
        moved.append(target)

    def unlink_that_fails(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError(f"{self.name} in use")

    monkeypatch.setattr(command, "move_into_place", fail_on_third)
    monkeypatch.setattr(Path, "unlink", unlink_that_fails)
    with capture_logs() as logs, pytest.raises(OSError, match="disk full"):
        run(folder)
    monkeypatch.undo()
    left = staging_warnings(logs)
    assert [entry["log_level"] for entry in left] == ["warning", "warning"]
    assert sorted(Path(str(entry["path"])).name for entry in left) == sorted(p.name for p in moved)


def write_sites_only(folder: Path) -> Path:
    """The folder ingest-eng's `--sites-only` writes: the sites layer, no receiver layer."""
    write_inputs(folder)
    (folder / "rx_real.parquet").unlink()
    return folder


def run_without_real_receivers(folder: Path) -> int:
    return main(["x72y429", "--folder", str(folder), "--no-real-receivers"])


@pytest.mark.usefixtures("env")
def test_without_real_receivers_the_grid_alone_is_written_and_marked_synthetic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = write_sites_only(tmp_path / "x72y429")
    with capture_logs() as logs:
        assert run_without_real_receivers(folder) == 0
    assert sorted(p.name for p in folder.iterdir()) == [
        "crossing.md",
        "rx_points.parquet",
        "tx_sites.parquet",
        "tx_sites_real.parquet",
        "x72y429.yaml",
    ]
    layer = gpd.read_parquet(folder / "rx_points.parquet")
    assert list(layer.columns) == ["rx_id", "geometry", "box_half_width_m"]
    assert len(layer) == 400
    assert layer["rx_id"].iloc[0] == "grid-r00c00"
    assert layer["rx_id"].str.startswith("grid-").all()
    assert (layer["box_half_width_m"] == 0.0).all()
    assert positions_marker(folder / "rx_points.parquet") == "synthetic"
    # The sites are still the real gateways, so their layer stays real.
    assert positions_marker(folder / "tx_sites.parquet") == "real"
    assert list(gpd.read_parquet(folder / "tx_sites.parquet")["site_id"]) == [SITE_A, SITE_B]
    out, err = capsys.readouterr()
    assert_no_real_coordinates(out + err + str(logs))


@pytest.mark.usefixtures("env")
def test_without_real_receivers_the_config_and_extent_pass_the_engines_checks(
    tmp_path: Path, snapshots_dir: Path
) -> None:
    folder = write_sites_only(tmp_path / "x72y429")
    assert run_without_real_receivers(folder) == 0
    config = load_config(folder / "x72y429.yaml")
    assert config.rx_points == folder / "rx_points.parquet"
    assert config.tx_sites == folder / "tx_sites.parquet"
    endpoints = read_points(config.tx_sites, "site_id") + read_points(config.rx_points, "rx_id")
    dem = named(snapshots_dir, DEM_ID)["dem"]
    check_sources({"dem": dem}, run_grid(endpoints, dem_resolution_m(dem)))


@pytest.mark.usefixtures("env")
def test_without_real_receivers_crossing_md_says_there_is_no_real_receiver(
    tmp_path: Path,
) -> None:
    folder = write_sites_only(tmp_path / "x72y429")
    assert run_without_real_receivers(folder) == 0
    text = (folder / "crossing.md").read_text(encoding="utf-8")
    real_section = text.split("## Paths between real endpoints")[1].split("## Grid paths")[0]
    assert "no real receiver" in real_section
    assert "|" not in real_section
    assert f"| {SITE_C} | gateway_only_gateway | left out |" in text
    for site in (SITE_A, SITE_B):
        assert f"| {site} | 400 |" in text
    assert NEVER_PUBLISHED in text
    assert_no_real_coordinates(text)


@pytest.mark.usefixtures("env")
def test_without_real_receivers_a_receiver_layer_in_the_folder_is_refused(tmp_path: Path) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    with capture_logs() as logs:
        assert run_without_real_receivers(folder) == 2
    (refused,) = [entry for entry in logs if entry["log_level"] == "error"]
    assert "rx_real.parquet" in str(refused["reason"])
    assert "--no-real-receivers" in str(refused["reason"])
    assert_no_real_coordinates(str(logs))
    assert sorted(p.name for p in folder.iterdir()) == ["rx_real.parquet", "tx_sites_real.parquet"]


@pytest.mark.usefixtures("env")
def test_without_the_flag_a_folder_with_no_receiver_layer_is_still_refused(
    tmp_path: Path,
) -> None:
    folder = write_sites_only(tmp_path / "x72y429")
    assert "rx_real.parquet" in refusal(folder)
    assert sorted(p.name for p in folder.iterdir()) == ["tx_sites_real.parquet"]


@pytest.mark.usefixtures("env")
def test_without_the_flag_a_receiver_layer_with_no_rows_is_refused(tmp_path: Path) -> None:
    folder = write_inputs(tmp_path / "x72y429")
    layer = gpd.read_parquet(folder / "rx_real.parquet")
    layer.iloc[0:0].to_parquet(folder / "rx_real.parquet")
    assert "rx_real.parquet" in refusal(folder)
    assert sorted(p.name for p in folder.iterdir()) == ["rx_real.parquet", "tx_sites_real.parquet"]


@pytest.mark.usefixtures("env")
def test_without_real_receivers_a_tile_other_than_x72y429_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Casey's engine_inputs (c), 2026-11-17, covers the x72y429 run only; a
    # second tile with a defined run would still need real receivers.
    monkeypatch.setitem(command.TILE_SNAPSHOTS, "x71y428", command.TILE_SNAPSHOTS["x72y429"])
    folder = write_sites_only(tmp_path / "x71y428")
    with capture_logs() as logs:
        assert main(["x71y428", "--folder", str(folder), "--no-real-receivers"]) == 2
    (refused,) = [entry for entry in logs if entry["log_level"] == "error"]
    assert "x71y428" in str(refused["reason"])
    assert "--no-real-receivers" in str(refused["reason"])
    assert_no_real_coordinates(str(logs))
    assert sorted(p.name for p in folder.iterdir()) == ["tx_sites_real.parquet"]


@pytest.mark.usefixtures("env")
def test_without_real_receivers_synthetic_sites_give_synthetic_outputs(tmp_path: Path) -> None:
    folder = write_sites_only(tmp_path / "x72y429")
    stamp_positions(folder / "tx_sites_real.parquet", "synthetic")
    assert run_without_real_receivers(folder) == 0
    assert positions_marker(folder / "tx_sites.parquet") == "synthetic"
    assert positions_marker(folder / "rx_points.parquet") == "synthetic"


def test_a_move_never_replaces_a_file_that_appeared_meanwhile(tmp_path: Path) -> None:
    source, target = tmp_path / "staged.md", tmp_path / "crossing.md"
    source.write_text("new\n", encoding="utf-8")
    target.write_text("appeared\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="appeared"):
        command.move_into_place(source, target)
    assert target.read_text(encoding="utf-8") == "appeared\n"
    assert source.read_text(encoding="utf-8") == "new\n"


# ADR 0008: real layers whose boxes are their lnk_02 edges.


def write_edge_inputs(folder: Path) -> Path:
    """The same made-up nodes, each as the 16-bit cell holding it, edges and centre."""
    import pyarrow as pa
    from test_terrain_edge_boxes import cell_box

    from engine.points import EDGE_COLUMNS

    folder.mkdir(parents=True, exist_ok=True)
    for name, id_column, rows, roles in (
        ("tx_sites_real.parquet", "site_id", SITES, [s[4] for s in SITES]),
        ("rx_real.parquet", "rx_id", [RX_ROW], None),
    ):
        cells = [cell_box(row[1], row[2]) for row in rows]
        layer = gpd.GeoDataFrame(
            {id_column: [row[0] for row in rows]},
            geometry=[Point(*cell.centre_xy_m) for cell in cells],
            crs="EPSG:26915",
        )
        layer.to_parquet(folder / name)
        table = pq.read_table(folder / name)
        for column in EDGE_COLUMNS:
            values = [getattr(cell, column.removeprefix("box_")) for cell in cells]
            table = table.append_column(column, pa.array(values, type=pa.int64()))
        if roles is not None:
            table = table.append_column("role", pa.array(roles))
        pq.write_table(table, folder / name)
    return folder


@pytest.mark.usefixtures("env")
def test_edge_layers_are_carried_into_the_run_layers(tmp_path: Path) -> None:
    from test_terrain_edge_boxes import cell_box

    from engine.points import EDGE_COLUMNS
    from terrain.draws import EdgeBox, EndpointBox

    folder = write_edge_inputs(tmp_path / "x72y429")
    assert run(folder) == 0

    receivers = gpd.read_parquet(folder / "rx_points.parquet")
    assert list(receivers.columns) == ["rx_id", "geometry", *EDGE_COLUMNS]
    assert len(receivers) == 401
    assert str(pq.read_schema(folder / "rx_points.parquet").field("box_south_i").type) == "int64"
    endpoints = read_points(folder / "rx_points.parquet", "rx_id")
    assert endpoints[0].box == cell_box(RX_ROW[1], RX_ROW[2])
    assert all(isinstance(e.box, EndpointBox) and not e.box.has_area for e in endpoints[1:])

    sites = read_points(folder / "tx_sites.parquet", "site_id")
    assert list(gpd.read_parquet(folder / "tx_sites.parquet").columns) == [
        "site_id",
        "geometry",
        *EDGE_COLUMNS,
    ]
    assert [s.endpoint_id for s in sites] == [SITE_A, SITE_B]
    assert all(isinstance(s.box, EdgeBox) for s in sites)
    crossing = (folder / "crossing.md").read_text(encoding="utf-8")
    assert "box reach" in crossing
