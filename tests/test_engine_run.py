"""The engine command end to end on the committed synthetic fixture (docs/INTERFACE.md).

Two runs of the fixture config into two output folders must give identical
`content_sha256` for every artifact (snp_01), the INTERFACE loading snippet
must read a run in a scratch folder with pyarrow and rasterio only, the
manifest is written last and the `.partial` folder renamed after it, and a
failed step leaves no manifest. The fixture is synthetic throughout; the
P.1812 numbers come from the synthetic map snapshot and are not predictions.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import rasterio
from engine_fixture import (
    FIXTURES,
    REPO,
    SNAPSHOTS,
    fixture_config,
    fixture_fields,
    fixture_settings,
    no_logger_caching,
)
from shapely.geometry import Point

import engine.run
from engine import MANIFEST_VERSION, SCHEMA_VERSION
from engine.errors import ConfigError
from engine.run import run_engine
from terrain.baselines import fspl_db

PREDICTION_COLUMNS = [
    "site_id",
    "rx_id",
    "distance_m",
    "bearing_deg",
    "fspl_db_median",
    "fspl_db_iqr",
    "knife_edge_db_median",
    "knife_edge_db_iqr",
    "hata_db_median",
    "hata_db_iqr",
    "p1812_db_median",
    "p1812_db_iqr",
    "los_share",
    "fresnel_clearance_pct_median",
    "clutter_height_m_median",
    "hata_variant",
    "endpoint_draws",
    "refused_draws",
    "seed",
    "tx_building_draws",
    "rx_building_draws",
    "p1812_at_cap_share",
    "geometry",
]


def engine_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "NODE_ID_HMAC_KEY"}
    env["SNAPSHOTS_DIR"] = str(SNAPSHOTS)
    return env


@pytest.fixture(scope="module")
def two_runs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """The fixture run in this process, then again in a fresh one through the command line."""
    first = run_engine(fixture_config(tmp_path_factory.mktemp("first")), fixture_settings())
    second_config = fixture_config(tmp_path_factory.mktemp("second"))
    done = subprocess.run(
        [sys.executable, "-m", "engine", "run", str(second_config)],
        cwd=REPO,
        env=engine_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return first, second_config.parent / "out" / "fixture"


def manifest_of(run: Path) -> dict[str, Any]:
    manifest: dict[str, Any] = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    return manifest


def content_hashes(run: Path) -> dict[str, str]:
    return {a["path"]: a["content_sha256"] for a in manifest_of(run)["artifacts"]}


def test_two_runs_give_identical_content_hashes(two_runs: tuple[Path, Path]) -> None:
    first, second = two_runs
    assert first != second
    assert content_hashes(first) == content_hashes(second)


def test_a_run_writes_every_interface_artifact(two_runs: tuple[Path, Path]) -> None:
    run = two_runs[0]
    coverage = {
        f"coverage_{site}_{model}.tif"
        for site in ("site-east", "site-west")
        for model in ("fspl", "knife_edge", "hata", "p1812")
    }
    expected = {
        "terrain/dem.tif",
        "terrain/dsm.tif",
        "terrain/canopy.tif",
        "terrain/clutter.tif",
        "predictions.parquet",
        "calibration.parquet",
        "calibration.md",
        "run_report.md",
    } | coverage
    assert set(content_hashes(run)) == expected
    on_disk = {p.relative_to(run).as_posix() for p in run.rglob("*") if p.is_file()}
    assert on_disk == expected | {"manifest.json"}
    assert run.name == "fixture"
    assert not (run.parent / "fixture.partial").exists()


def test_artifact_file_hashes_match_the_files(two_runs: tuple[Path, Path]) -> None:
    run = two_runs[0]
    for artifact in manifest_of(run)["artifacts"]:
        digest = hashlib.sha256((run / artifact["path"]).read_bytes()).hexdigest()
        assert artifact["sha256"] == digest, artifact["path"]


def test_predictions_hold_every_interface_column(two_runs: tuple[Path, Path]) -> None:
    predictions = gpd.read_parquet(two_runs[0] / "predictions.parquet")
    assert list(predictions.columns) == PREDICTION_COLUMNS
    assert predictions.crs.to_epsg() == 26915
    assert len(predictions) == 1000
    assert set(predictions.geom_type) == {"LineString"}
    assert not predictions.duplicated(["site_id", "rx_id"]).any()
    assert list(predictions["site_id"].iloc[:2]) == ["site-east", "site-east"]


def test_prediction_values_obey_the_interface_rules(two_runs: tuple[Path, Path]) -> None:
    rows = gpd.read_parquet(two_runs[0] / "predictions.parquet")
    draws = rows["endpoint_draws"]
    assert set(draws) == {1, 16}
    assert ((rows["refused_draws"] >= 0) & (rows["refused_draws"] <= draws)).all()
    assert rows["refused_draws"].sum() > 0, "the DEM hole must refuse some draws"
    for end in ("tx_building_draws", "rx_building_draws"):
        assert ((rows[end] >= 0) & (rows[end] <= draws)).all()
    assert rows["rx_building_draws"].sum() > 0
    kept = rows[rows["fspl_db_median"].notna()]
    assert (kept["knife_edge_db_median"] >= kept["fspl_db_median"] - 1e-9).all()
    assert (kept["clutter_height_m_median"] >= 0.0).all()
    assert kept["los_share"].between(0.0, 1.0).all()
    assert kept["p1812_at_cap_share"].between(0.0, 1.0).all()
    assert set(rows["hata_variant"]) == {"urban", "suburban"}
    assert rows["bearing_deg"].between(0.0, 360.0, inclusive="left").all()


def test_a_single_draw_row_has_the_closed_form_free_space_loss(
    two_runs: tuple[Path, Path],
) -> None:
    rows = gpd.read_parquet(two_runs[0] / "predictions.parquet")
    single = rows[(rows["endpoint_draws"] == 1) & (rows["refused_draws"] == 0)].iloc[0]
    assert single["fspl_db_median"] == pytest.approx(
        fspl_db(single["distance_m"], 906.875), abs=0.01
    )
    assert single["fspl_db_iqr"] == 0.0
    assert single["geometry"].length == pytest.approx(single["distance_m"])


def interface_snippet() -> str:
    text = (REPO / "docs" / "INTERFACE.md").read_text(encoding="utf-8")
    section = text.split("## Loading without the database", 1)[1]
    match = re.search(r"```python\n(.*?)```", section, re.DOTALL)
    assert match is not None
    return match.group(1)


def test_the_interface_loading_snippet_reads_the_run(
    two_runs: tuple[Path, Path], tmp_path: Path
) -> None:
    snippet = interface_snippet().replace('"out/test-tile"', repr(two_runs[0].as_posix()))
    result = subprocess.run(
        [sys.executable, "-I", "-c", snippet],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "EPSG:26915" in result.stdout
    assert "(10.0, 10.0)" in result.stdout


def test_the_manifest_records_versions_seed_snapshots_inputs_and_code(
    two_runs: tuple[Path, Path],
) -> None:
    manifest = manifest_of(two_runs[0])
    assert manifest["manifest_version"] == MANIFEST_VERSION == "1.1.0"
    assert manifest["schema_version"] == SCHEMA_VERSION == "1.2.0"
    assert manifest["run_id"] == "fixture"
    assert manifest["seed"] == 20261108
    assert set(manifest["models"]) == {"fspl", "knife_edge", "hata", "p1812"}
    ids = {s["id"] for s in manifest["snapshots"]}
    assert ids == set(fixture_fields()["snapshots"].values())
    for snapshot in manifest["snapshots"]:
        manifest_bytes = (SNAPSHOTS / snapshot["id"] / "manifest.json").read_bytes()
        assert snapshot["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    # Inputs are recorded as the config names them; the test config names them in full.
    inputs = {i["path"] for i in manifest["inputs"]}
    names = {Path(path).name for path in inputs}
    assert {"two_sites.parquet", "receivers_500.parquet", "observations.parquet"} <= names
    assert {"profiles/site_gateway.yaml", "profiles/mesh_node.yaml"} <= inputs
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["engine_git_commit"])
    assert isinstance(manifest["engine_git_dirty"], bool)
    assert manifest["py1812_commit"] == "6c9061dd022e93c84169059a016643926ce89964"
    assert manifest["py1812_archive_sha256"].startswith("03d7c5bd")
    assert manifest["itm_library_sha256"] is None
    assert manifest["itm_source_commit"] is None
    rows = [a for a in manifest["artifacts"] if a["path"] == "predictions.parquet"]
    assert rows[0]["rows"] == 1000


def test_the_run_report_breaks_down_refusals_and_canopy_gaps(two_runs: tuple[Path, Path]) -> None:
    report = (two_runs[0] / "run_report.md").read_text(encoding="utf-8")
    rows = gpd.read_parquet(two_runs[0] / "predictions.parquet")
    for cause in ("missing map data", "empty 10 m cell", "missing clutter", "too-short profile"):
        assert cause in report
    assert f"| all causes | {int(rows['refused_draws'].sum())} |" in report
    assert "canopy_east.tif" in report and "canopy_west.tif" in report
    assert "| canopy_east.tif | 900 |" in report
    assert "No corridors" in report
    assert "DSM gaps filled" in report
    assert "between 0.15 and 0.25" in report


def test_calibration_reports_residuals_per_model(two_runs: tuple[Path, Path]) -> None:
    run = two_runs[0]
    calibration = pd.read_parquet(run / "calibration.parquet")
    predictions = gpd.read_parquet(run / "predictions.parquet")
    assert len(calibration) == 100
    assert set(calibration["run_id"]) == {"fixture"}
    joined = calibration.merge(predictions, on=["site_id", "rx_id"])
    residual = joined["observed_loss_db"] - joined["hata_db_median"]
    assert np.allclose(joined["hata_residual_db"], residual, equal_nan=True)
    markdown = (run / "calibration.md").read_text(encoding="utf-8")
    assert "fixture" in markdown
    for model in ("fspl", "knife_edge", "hata", "p1812"):
        assert f"| {model} |" in markdown


def test_coverage_files_are_cogs_on_the_run_grid(two_runs: tuple[Path, Path]) -> None:
    with rasterio.open(two_runs[0] / "coverage_site-east_fspl.tif") as coverage:
        assert coverage.crs.to_epsg() == 26915
        assert coverage.res == (10.0, 10.0)
        assert coverage.nodata is not None
        assert coverage.transform.c % 10.0 == 0.0 and coverage.transform.f % 10.0 == 0.0
        assert coverage.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"


def site_east_xy_m() -> tuple[float, float]:
    """site-east as the engine sees it: converted from EPSG:4326, not exactly 725 850."""
    sites = gpd.read_parquet(FIXTURES / "two_sites.parquet").to_crs(epsg=26915)
    point = sites[sites["site_id"] == "site-east"].geometry.iloc[0]
    return float(point.x), float(point.y)


def small_receivers(folder: Path, *, at_site: bool = False) -> Path:
    points = [Point(725_400.0, 4_285_300.0), Point(725_700.0, 4_285_800.0)]
    if at_site:
        points.append(Point(*site_east_xy_m()))
    layer = gpd.GeoDataFrame(
        {"rx_id": [f"rx-{i}" for i in range(len(points))]}, geometry=points, crs="EPSG:26915"
    )
    path = folder / ("receivers_at_site.parquet" if at_site else "receivers.parquet")
    layer.to_parquet(path, index=False)
    return path


def small_config(folder: Path, **overrides: Any) -> Path:
    fields: dict[str, Any] = {
        "rx_points": str(small_receivers(folder)),
        "calibration": None,
        "models": ["fspl", "knife_edge"],
    }
    fields.update(overrides)
    return fixture_config(folder, **fields)


def test_the_manifest_is_written_last_then_the_folder_renamed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = engine.run.write_run_manifest
    seen: dict[str, Any] = {}

    def spy(partial: Path, *args: Any, **kwargs: Any) -> Path:
        seen["partial"] = partial
        seen["files_before"] = sorted(p.name for p in partial.rglob("*") if p.is_file())
        seen["final_existed"] = (partial.parent / "fixture").exists()
        return real(partial, *args, **kwargs)

    monkeypatch.setattr(engine.run, "write_run_manifest", spy)
    final = run_engine(small_config(tmp_path), fixture_settings())
    assert seen["partial"].name == "fixture.partial"
    assert "manifest.json" not in seen["files_before"]
    assert "predictions.parquet" in seen["files_before"]
    assert seen["final_existed"] is False
    assert final == tmp_path / "out" / "fixture"
    assert (final / "manifest.json").is_file()
    assert not seen["partial"].exists()


def test_a_failed_step_leaves_no_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("coverage step failed on purpose")

    monkeypatch.setattr(engine.run, "write_coverage", broken)
    with pytest.raises(RuntimeError, match="on purpose"):
        run_engine(small_config(tmp_path), fixture_settings())
    partial = tmp_path / "out" / "fixture.partial"
    assert partial.is_dir()
    assert (partial / "predictions.parquet").is_file()
    assert not (partial / "manifest.json").exists()
    assert not (tmp_path / "out" / "fixture").exists()


@pytest.mark.parametrize("existing", ["fixture", "fixture.partial"])
def test_an_existing_run_folder_is_refused_and_left_alone(tmp_path: Path, existing: str) -> None:
    folder = tmp_path / "out" / existing
    folder.mkdir(parents=True)
    (folder / "keep.txt").write_text("Casey decides", encoding="utf-8")
    with pytest.raises(ConfigError, match=existing):
        run_engine(small_config(tmp_path), fixture_settings())
    assert (folder / "keep.txt").read_text(encoding="utf-8") == "Casey decides"
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == [existing]


def test_a_zero_length_pair_is_a_row_of_nulls(tmp_path: Path) -> None:
    config = small_config(tmp_path, rx_points=str(small_receivers(tmp_path, at_site=True)))
    rows = gpd.read_parquet(run_engine(config, fixture_settings()) / "predictions.parquet")
    same = rows[(rows["site_id"] == "site-east") & (rows["rx_id"] == "rx-2")].iloc[0]
    assert same["distance_m"] == 0.0
    assert pd.isna(same["bearing_deg"]) and pd.isna(same["fspl_db_median"])
    assert same["endpoint_draws"] == 1
    assert same["refused_draws"] == same["endpoint_draws"], "within 0 to endpoint_draws"
    assert pd.isna(same["rx_building_draws"]) and pd.isna(same["clutter_height_m_median"])
    assert rows[rows["rx_id"] != "rx-2"]["fspl_db_median"].notna().all()


def copy_snapshots(root: Path) -> Path:
    shutil.copytree(SNAPSHOTS, root)
    return root


def test_a_snapshot_that_differs_from_its_manifest_is_refused(tmp_path: Path) -> None:
    root = copy_snapshots(tmp_path / "snapshots")
    with (root / "dsm-synthetic-subtile" / "README.md").open("a", encoding="utf-8") as readme:
        readme.write("edited\n")
    with pytest.raises(ConfigError, match="dsm-synthetic-subtile"):
        run_engine(small_config(tmp_path), fixture_settings(snapshots_dir=root))
    assert not (tmp_path / "out").exists()


def test_a_missing_snapshot_is_refused(tmp_path: Path) -> None:
    snapshots = dict(fixture_fields()["snapshots"], canopy="canopy-not-written")
    with pytest.raises(ConfigError, match="canopy-not-written"):
        run_engine(small_config(tmp_path, snapshots=snapshots), fixture_settings())


@pytest.mark.parametrize(
    ("layer", "message"),
    [
        (
            gpd.GeoDataFrame(
                {"rx_id": ["a", "a"]}, geometry=[Point(725_400, 4_285_300)] * 2, crs="EPSG:26915"
            ),
            "unique",
        ),
        (
            gpd.GeoDataFrame(
                {"rx_id": ["a"]}, geometry=[Point(725_400, 4_285_300)], crs="EPSG:3857"
            ),
            "EPSG:4326 or EPSG:26915",
        ),
        (
            gpd.GeoDataFrame(
                {"name": ["a"]}, geometry=[Point(725_400, 4_285_300)], crs="EPSG:26915"
            ),
            "rx_id",
        ),
        (
            gpd.GeoDataFrame(
                {"rx_id": ["a"], "box_half_width_m": [-1.0]},
                geometry=[Point(725_400, 4_285_300)],
                crs="EPSG:26915",
            ),
            "box_half_width_m",
        ),
        (
            gpd.GeoDataFrame(
                {"rx_id": ["a"]}, geometry=[Point(725_400, 4_285_300).buffer(5)], crs="EPSG:26915"
            ),
            "points",
        ),
    ],
)
def test_point_layer_refusals(tmp_path: Path, layer: gpd.GeoDataFrame, message: str) -> None:
    path = tmp_path / "bad.parquet"
    layer.to_parquet(path, index=False)
    with pytest.raises(ConfigError, match=message):
        run_engine(small_config(tmp_path, rx_points=str(path)), fixture_settings())
    assert not (tmp_path / "out").exists()


def test_points_outside_the_dem_are_refused(tmp_path: Path) -> None:
    layer = gpd.GeoDataFrame(
        {"rx_id": ["far"]}, geometry=[Point(735_000.0, 4_285_300.0)], crs="EPSG:26915"
    )
    path = tmp_path / "far.parquet"
    layer.to_parquet(path, index=False)
    with pytest.raises(ConfigError, match="outside the DEM"):
        run_engine(small_config(tmp_path, rx_points=str(path)), fixture_settings())
    assert not (tmp_path / "out").exists()


def metro_snapshot(root: Path) -> str:
    """A 10 m DEM snapshot averaged from the fixture's 1 m DEM, as a stand-in metro DEM."""
    from pipeline.common.snapshot import finish_snapshot, start_snapshot, write_manifest

    partial = start_snapshot(root, "dem-10m-synthetic")
    with rasterio.open(SNAPSHOTS / "dem-1m-synthetic-subtile" / "dem.tif") as fine:
        heights = fine.read(1, masked=True).filled(np.nan).reshape(100, 10, 100, 10)
        profile = fine.profile | {"height": 100, "width": 100}
        profile["transform"] = fine.transform @ fine.transform.scale(10.0, 10.0)
    coarse = np.nan_to_num(np.nanmean(heights, axis=(1, 3)), nan=-999999.0).astype("float32")
    with rasterio.open(partial / "dem.tif", "w", **profile) as dst:
        dst.write(coarse, 1)
    metadata = {
        "snapshot_id": "dem-10m-synthetic",
        "capture_date": "2026-11-15",
        "source_url": "none: test",
        "licence": "MIT",
        "seeds": {},
    }
    write_manifest(partial, metadata)
    finish_snapshot(partial)
    return "dem-10m-synthetic"


def test_a_metro_dem_run_writes_null_clutter_building_and_p1812_columns(tmp_path: Path) -> None:
    root = copy_snapshots(tmp_path / "snapshots")
    snapshots = {
        "dem": metro_snapshot(root),
        "dsm": None,
        "canopy": None,
        "buildings": "osm-buildings-synthetic",
        "p1812_maps": None,
    }
    config = small_config(tmp_path, models=["fspl", "knife_edge", "hata"], snapshots=snapshots)
    run = run_engine(config, fixture_settings(snapshots_dir=root))
    rows = gpd.read_parquet(run / "predictions.parquet")
    assert "p1812_db_median" not in rows.columns
    for column in (
        "clutter_height_m_median",
        "tx_building_draws",
        "rx_building_draws",
        "p1812_at_cap_share",
    ):
        assert rows[column].isna().all(), column
    assert rows["hata_variant"].notna().all()
    assert rows["fspl_db_median"].notna().all()
    assert sorted(p.name for p in (run / "terrain").iterdir()) == ["dem.tif"]
    with rasterio.open(run / "terrain" / "dem.tif") as dem:
        assert dem.res == (10.0, 10.0)


def test_the_command_line_exits_zero_on_a_run_and_two_on_a_refusal(tmp_path: Path) -> None:
    env = engine_env()
    good = small_config(tmp_path)
    command = [sys.executable, "-m", "engine", "run"]
    done = subprocess.run(
        [*command, str(good)], cwd=REPO, env=env, capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "out" / "fixture" / "manifest.json").is_file()
    refused = subprocess.run(
        [*command, str(good)], cwd=REPO, env=env, capture_output=True, text=True, check=False
    )
    assert refused.returncode == 2
    assert "exists" in refused.stderr


def itm_library() -> Path:
    from engine.settings import EngineSettings

    return EngineSettings(snapshots_dir=SNAPSHOTS).itm_library_path


@pytest.mark.skipif(not itm_library().is_file(), reason="libitm.so exists only in the image")
def test_the_itm_fixture_runs_in_the_image(tmp_path: Path) -> None:
    from engine.settings import EngineSettings

    config = small_config(tmp_path, models=["fspl", "itm"])
    run = run_engine(
        config,
        fixture_settings(
            **EngineSettings(snapshots_dir=SNAPSHOTS).model_dump(
                include={"itm_library_path", "itm_source_commit", "engine_git_commit"}
            )
        ),
    )
    rows = gpd.read_parquet(run / "predictions.parquet")
    assert rows["itm_db_median"].notna().all()
    assert re.fullmatch(r"[0-9a-f]{64}", manifest_of(run)["itm_library_sha256"])
    assert re.fullmatch(r"[0-9a-f]{40}", manifest_of(run)["itm_source_commit"])


def test_canopy_is_resampled_by_nearest_neighbour(two_runs: tuple[Path, Path]) -> None:
    from pyproj import Transformer

    to_3857 = Transformer.from_crs("EPSG:26915", "EPSG:3857", always_xy=True)
    tiles = SNAPSHOTS / "canopy-synthetic-subtile"
    with rasterio.open(two_runs[0] / "terrain" / "canopy.tif") as canopy:
        for x_m, y_m in [(725_200.5, 4_285_300.5), (725_420.5, 4_285_250.5)]:
            row, col = canopy.index(x_m, y_m)
            built = canopy.read(1, window=((row, row + 1), (col, col + 1)))[0, 0]
            with rasterio.open(tiles / "canopy_west.tif") as tile:
                source = next(tile.sample([to_3857.transform(x_m, y_m)]))[0]
            assert built == float(source)


def test_clutter_follows_the_rule_cell_by_cell(two_runs: tuple[Path, Path]) -> None:
    from terrain.clutter import clutter_height_m

    terrain = two_runs[0] / "terrain"
    window = ((380, 420), (580, 620))
    layers = {}
    for name in ("dem", "dsm", "canopy", "clutter"):
        with rasterio.open(terrain / f"{name}.tif") as layer:
            layers[name] = layer.read(1, window=window, masked=True).filled(np.nan)
    expected = clutter_height_m(layers["dem"], dsm_m=layers["dsm"], canopy_m=layers["canopy"])
    assert np.array_equal(layers["clutter"], expected.astype("float32"), equal_nan=True)
    assert np.isnan(layers["clutter"]).any(), "the DEM hole stays without a value"


# Review of PR #40: decisions B2, B3, B5 and should-fix items S1 to S5.

MODEL_COLUMNS = [
    "fspl_db_median",
    "knife_edge_db_median",
    "hata_db_median",
    "p1812_db_median",
    "los_share",
    "fresnel_clearance_pct_median",
    "clutter_height_m_median",
    "p1812_at_cap_share",
]


def test_columns_are_null_exactly_where_every_draw_was_refused(
    two_runs: tuple[Path, Path],
) -> None:
    rows = gpd.read_parquet(two_runs[0] / "predictions.parquet")
    kept = rows["refused_draws"] < rows["endpoint_draws"]
    assert int(kept.sum()) == 965
    for column in MODEL_COLUMNS:
        assert (rows[column].notna() == kept).all(), column
    assert rows["hata_variant"].notna().all()
    assert rows["tx_building_draws"].notna().all() and rows["rx_building_draws"].notna().all()


def endpoint_boxes() -> tuple[dict[str, Any], dict[str, Any]]:
    from engine.points import read_points

    sites = {e.endpoint_id: e.box for e in read_points(FIXTURES / "two_sites.parquet", "site_id")}
    receivers = read_points(FIXTURES / "receivers_500.parquet", "rx_id")
    return sites, {e.endpoint_id: e.box for e in receivers}


def test_the_hata_variant_comes_from_the_samplers_own_draws(two_runs: tuple[Path, Path]) -> None:
    from engine.buildings import Footprints
    from engine.predict import link_id
    from terrain.baselines import hata_variant
    from terrain.draws import draw_endpoints, draw_seed

    rows = gpd.read_parquet(two_runs[0] / "predictions.parquet")
    footprints = Footprints.from_parquet(
        SNAPSHOTS / "osm-buildings-synthetic" / "buildings.parquet"
    )
    sites, receivers = endpoint_boxes()
    boxed = rows[rows["endpoint_draws"] == 16].head(12)
    assert len(boxed) == 12
    for _, row in boxed.iterrows():
        seed = draw_seed(20261108, link_id(row["site_id"], row["rx_id"]))
        pairs = draw_endpoints(sites[row["site_id"]], receivers[row["rx_id"]], 16, seed)
        tx_share = float(np.median([footprints.share_within(t) for t, _ in pairs]))
        rx_share = float(np.median([footprints.share_within(r) for _, r in pairs]))
        assert row["hata_variant"] == hata_variant(tx_share, rx_share)


def test_a_run_without_p1812_never_imports_it(tmp_path: Path) -> None:
    config = small_config(tmp_path, models=["fspl", "knife_edge"])
    script = (
        "import sys; from pathlib import Path\n"
        f"sys.path.insert(0, {str(REPO / 'tests')!r})\n"
        "from engine_fixture import fixture_settings\n"
        "from engine.run import run_engine\n"
        f"run_engine(Path({str(config)!r}), fixture_settings())\n"
        "print('terrain.p1812' in sys.modules, 'Py1812.P1812' in sys.modules)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO,
        env=engine_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().splitlines()[-1] == "False False"


def test_a_value_error_in_a_draw_fails_the_run_and_is_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from structlog.testing import capture_logs

    def broken_builder(*args: Any) -> Any:
        def build(tx_xy_m: Any, rx_xy_m: Any) -> Any:
            raise ValueError("a sampler bug, not a refusal")

        return build

    monkeypatch.setattr(engine.run, "_builder", broken_builder)
    with capture_logs() as logs, pytest.raises(ValueError, match="sampler bug"):
        run_engine(small_config(tmp_path), fixture_settings())
    partial = tmp_path / "out" / "fixture.partial"
    assert partial.is_dir() and not (partial / "manifest.json").exists()
    failed = [entry for entry in logs if entry["event"] == "engine_run_failed"]
    assert failed and failed[0]["run_id"] == "fixture"
    assert failed[0]["partial"] == str(partial)


def test_canopy_matches_nearest_neighbour_over_a_whole_window(two_runs: tuple[Path, Path]) -> None:
    from pyproj import Transformer

    to_3857 = Transformer.from_crs("EPSG:26915", "EPSG:3857", always_xy=True)
    with rasterio.open(two_runs[0] / "terrain" / "canopy.tif") as canopy:
        window = ((640, 720), (100, 180))
        built = canopy.read(1, window=window, masked=True).filled(np.nan)
        rows, cols = np.mgrid[640:720, 100:180]
        x_m, y_m = rasterio.transform.xy(canopy.transform, rows.ravel(), cols.ravel())
    with rasterio.open(SNAPSHOTS / "canopy-synthetic-subtile" / "canopy_west.tif") as tile:
        points = list(zip(*to_3857.transform(np.asarray(x_m), np.asarray(y_m)), strict=True))
        source = np.array([value[0] for value in tile.sample(points)], dtype=float)
    assert built.size == 6400 and np.unique(built).size > 3, "the window must cross crowns"
    assert np.array_equal(built.ravel(), source)


def near_site_receivers(folder: Path, offset_m: float) -> Path:
    x_m, y_m = site_east_xy_m()
    layer = gpd.GeoDataFrame(
        {"rx_id": ["near", "far"]},
        geometry=[Point(x_m - offset_m, y_m), Point(725_400.0, 4_285_300.0)],
        crs="EPSG:26915",
    )
    path = folder / "near.parquet"
    layer.to_parquet(path, index=False)
    return path


def test_a_draw_without_p1812_cell_interior_keeps_its_losses_without_p1812(tmp_path: Path) -> None:
    config = small_config(tmp_path, rx_points=str(near_site_receivers(tmp_path, 5.0)))
    rows = gpd.read_parquet(run_engine(config, fixture_settings()) / "predictions.parquet")
    near = rows[(rows["site_id"] == "site-east") & (rows["rx_id"] == "near")].iloc[0]
    assert near["refused_draws"] == 0
    assert near["fspl_db_median"] == pytest.approx(fspl_db(5.0, 906.875), abs=0.01)
    assert pd.isna(near["clutter_height_m_median"])
    assert rows[rows["rx_id"] == "far"]["clutter_height_m_median"].notna().all()


def test_a_draw_without_p1812_cell_interior_is_refused_with_p1812(tmp_path: Path) -> None:
    config = small_config(
        tmp_path, rx_points=str(near_site_receivers(tmp_path, 5.0)), models=["fspl", "p1812"]
    )
    rows = gpd.read_parquet(run_engine(config, fixture_settings()) / "predictions.parquet")
    near = rows[(rows["site_id"] == "site-east") & (rows["rx_id"] == "near")].iloc[0]
    assert near["refused_draws"] == near["endpoint_draws"] == 1
    assert pd.isna(near["fspl_db_median"])
    report = (tmp_path / "out" / "fixture" / "run_report.md").read_text(encoding="utf-8")
    assert "| too-short profile | 1 |" in report


def test_building_draws_are_counted_on_a_1_m_dem_without_clutter_layers(tmp_path: Path) -> None:
    layer = gpd.GeoDataFrame(
        {"rx_id": ["in-building", "in-street"]},
        geometry=[Point(725_620.3, 4_285_670.3), Point(725_400.0, 4_285_300.0)],
        crs="EPSG:26915",
    )
    path = tmp_path / "buildings_rx.parquet"
    layer.to_parquet(path, index=False)
    snapshots = dict(fixture_fields()["snapshots"], dsm=None, canopy=None, p1812_maps=None)
    config = small_config(
        tmp_path, rx_points=str(path), models=["fspl", "hata"], snapshots=snapshots
    )
    run = run_engine(config, fixture_settings())
    rows = gpd.read_parquet(run / "predictions.parquet").set_index(["site_id", "rx_id"])
    assert rows["rx_building_draws"].notna().all() and rows["tx_building_draws"].notna().all()
    assert rows.loc[("site-east", "in-building"), "rx_building_draws"] == 1
    assert rows.loc[("site-east", "in-street"), "rx_building_draws"] == 0
    assert rows["clutter_height_m_median"].isna().all()


def write_test_snapshot(root: Path, snapshot_id: str, files: dict[str, Any]) -> str:
    from pipeline.common.snapshot import finish_snapshot, start_snapshot, write_manifest

    partial = start_snapshot(root, snapshot_id)
    for name, write in files.items():
        write(partial / name)
    metadata = {
        "snapshot_id": snapshot_id,
        "capture_date": "2026-11-15",
        "source_url": "none: test",
        "licence": "MIT",
        "seeds": {},
    }
    write_manifest(partial, metadata)
    finish_snapshot(partial)
    return snapshot_id


def shifted_copy(source: Path, shift_m: float) -> Any:
    def write(path: Path) -> None:
        with rasterio.open(source) as tile:
            profile = tile.profile
            profile["transform"] = rasterio.Affine.translation(shift_m, 0.0) @ tile.transform
            with rasterio.open(path, "w", **profile) as dst:
                dst.write(tile.read())

    return write


def copy_file(source: Path) -> Any:
    return lambda path: shutil.copyfile(source, path)


def test_a_canopy_tile_outside_the_run_extent_is_reported_as_outside(tmp_path: Path) -> None:
    root = copy_snapshots(tmp_path / "snapshots")
    tiles = SNAPSHOTS / "canopy-synthetic-subtile"
    canopy = write_test_snapshot(
        root,
        "canopy-three-tiles",
        {
            "canopy_east.tif": copy_file(tiles / "canopy_east.tif"),
            "canopy_west.tif": copy_file(tiles / "canopy_west.tif"),
            "canopy_far.tif": shifted_copy(tiles / "canopy_west.tif", 200_000.0),
        },
    )
    snapshots = dict(fixture_fields()["snapshots"], canopy=canopy)
    run = run_engine(
        small_config(tmp_path, snapshots=snapshots), fixture_settings(snapshots_dir=root)
    )
    report = (run / "run_report.md").read_text(encoding="utf-8")
    assert "| canopy_far.tif | outside the run extent |" in report
    assert "| canopy_east.tif | 900 |" in report


def test_unreadable_inputs_are_refusals_before_any_write(tmp_path: Path) -> None:
    not_parquet = tmp_path / "not.parquet"
    not_parquet.write_text("site_id,rx_id\n", encoding="utf-8")
    for field in ("rx_points", "calibration"):
        with pytest.raises(ConfigError, match="not.parquet"):
            run_engine(small_config(tmp_path, **{field: str(not_parquet)}), fixture_settings())
    assert not (tmp_path / "out").exists()


def test_a_bad_p1812_map_snapshot_is_a_refusal_before_any_write(tmp_path: Path) -> None:
    root = copy_snapshots(tmp_path / "snapshots")
    maps = write_test_snapshot(
        root,
        "p1812-maps-wrong-grid",
        {name: (lambda p: p.write_text("40 40\n40 40\n")) for name in ("DN50.TXT", "N050.TXT")},
    )
    snapshots = dict(fixture_fields()["snapshots"], p1812_maps=maps)
    config = small_config(tmp_path, models=["fspl", "p1812"], snapshots=snapshots)
    with pytest.raises(ConfigError, match="121 by 241"):
        run_engine(config, fixture_settings(snapshots_dir=root))
    assert not (tmp_path / "out").exists()


def test_a_dem_whose_pixels_do_not_divide_10_m_is_a_refusal(tmp_path: Path) -> None:
    root = copy_snapshots(tmp_path / "snapshots")

    def three_metre_dem(path: Path) -> None:
        profile = {
            "driver": "GTiff",
            "height": 400,
            "width": 400,
            "count": 1,
            "dtype": "float32",
            "crs": "EPSG:26915",
            "nodata": -9999.0,
            "transform": rasterio.transform.from_origin(724_800.0, 4_286_200.0, 3.0, 3.0),
        }
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(np.full((1, 400, 400), 150.0, dtype="float32"))

    dem = write_test_snapshot(root, "dem-3m", {"dem.tif": three_metre_dem})
    snapshots = {"dem": dem, "dsm": None, "canopy": None, "buildings": None, "p1812_maps": None}
    config = small_config(tmp_path, snapshots=snapshots)
    with pytest.raises(ConfigError, match="10 m"):
        run_engine(config, fixture_settings(snapshots_dir=root))


def test_the_engine_commit_fallback_logs_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    from structlog.testing import capture_logs

    import engine.provenance

    def no_git(*arguments: str) -> str:
        raise OSError("git is not installed")

    monkeypatch.setattr(engine.provenance, "_git", no_git)
    with capture_logs() as logs:
        commit, dirty = engine.provenance.engine_commit(
            fixture_settings(engine_git_commit="a" * 40)
        )
    assert (commit, dirty) == ("a" * 40, None)
    assert any(
        e["event"] == "engine_commit_from_environment" and e["log_level"] == "warning" for e in logs
    )


def test_the_seed_column_holds_each_pairs_draw_seed(two_runs: tuple[Path, Path]) -> None:
    """Casey on PR #42: predictions.parquet gets the per-pair seed, as link_terrain does."""
    import pyarrow.parquet as pq

    from engine.predict import link_id
    from terrain.draws import draw_seed

    table = pq.read_table(two_runs[0] / "predictions.parquet")
    assert str(table.schema.field("seed").type) == "int64"
    rows = gpd.read_parquet(two_runs[0] / "predictions.parquet")
    assert rows["seed"].notna().all()
    for _, row in rows.iloc[[0, 1, 517, 999]].iterrows():
        assert row["seed"] == draw_seed(20261108, link_id(row["site_id"], row["rx_id"]))


def test_the_command_refuses_to_run_without_snapshots_dir(tmp_path: Path) -> None:
    """No default hides in code (INTERFACE): an unset SNAPSHOTS_DIR is a refusal, exit 2."""
    env = {k: v for k, v in engine_env().items() if k != "SNAPSHOTS_DIR"}
    config = small_config(tmp_path)
    done = subprocess.run(
        [sys.executable, "-m", "engine", "run", str(config)],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 2
    assert "SNAPSHOTS_DIR" in done.stderr
    assert not (tmp_path / "out").exists()


# The import above activates the autouse fixture for this module.
_ = no_logger_caching
