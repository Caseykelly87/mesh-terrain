"""web/figures/coverage_png.py, the README's coverage figure from one engine coverage file.

The colour classes are a pure function and are tested value by value. The
synthetic-run guard is tested on a tiny run folder written here, with a 4 by 4
coverage GeoTIFF, two stamped point layers and a manifest; nothing is read
from a real run. The rendered PNG is checked by eye in the PR, so the one
end-to-end test below only checks that a paletted PNG with a transparent
nodata entry is written.
"""

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import rasterio
from rasterio.transform import from_origin

from engine.positions_marker import stamp_positions

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "web" / "figures" / "coverage_png.py"
COVERAGE_NAME = "coverage_site-a_knife_edge.tif"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("coverage_png", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


figure = load_script()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# Classes: 0 nodata, 1 below 60 dB, 2 for [60, 70), ..., 6 for [100, 110), 7 from 110 up.
@pytest.mark.parametrize(
    ("value_db", "expected"),
    [
        (12.0, 1),
        (59.99, 1),
        (60.0, 2),
        (69.99, 2),
        (70.0, 3),
        (85.0, 4),
        (99.99, 5),
        (100.0, 6),
        (109.99, 6),
        (110.0, 7),
        (250.0, 7),
        (float("nan"), 0),
        (float("inf"), 0),
        (float("-inf"), 0),
    ],
)
def test_class_index_of_each_value(value_db: float, expected: int) -> None:
    indices = figure.class_indices(np.array([[value_db]]))
    assert indices.dtype == np.uint8
    assert indices.tolist() == [[expected]]


def test_class_indices_keep_the_array_shape() -> None:
    values = np.array([[55.0, np.nan, 75.0], [105.0, 115.0, 65.0]])
    assert figure.class_indices(values).tolist() == [[1, 0, 3], [6, 7, 2]]


@pytest.mark.parametrize("edges_db", [(), (70.0, 60.0), (60.0, 60.0), (60.0, float("nan"))])
def test_class_indices_refuse_edges_that_do_not_increase(edges_db: tuple[float, ...]) -> None:
    with pytest.raises(ValueError, match="edges"):
        figure.class_indices(np.array([[70.0]]), edges_db)


def test_colour_table_makes_nodata_transparent_and_every_class_opaque() -> None:
    table = figure.colour_table()
    assert table[0] == (0, 0, 0, 0)
    classes = [table[index] for index in range(1, len(figure.EDGES_DB) + 2)]
    assert all(alpha == 255 for *_, alpha in classes)
    assert len(set(classes)) == len(classes)
    assert sorted(table) == list(range(len(figure.EDGES_DB) + 2))


def test_colour_table_reads_hex_colours() -> None:
    table = figure.colour_table(("#fde725", "#440154"))
    assert table == {0: (0, 0, 0, 0), 1: (253, 231, 37, 255), 2: (68, 1, 84, 255)}


def test_lowest_loss_class_is_the_lightest() -> None:
    # Viridis runs dark to light; the strongest signal gets the lightest colour.
    lightness = [sum(rgb) for *rgb, _ in (figure.colour_table()[i] for i in range(1, 8))]
    assert lightness == sorted(lightness, reverse=True)


def test_class_labels_name_every_class_with_units() -> None:
    assert figure.class_labels((60.0, 70.0, 80.0)) == [
        "below 60",
        "60 to 70",
        "70 to 80",
        "80 or more",
    ]


def test_legend_table_lists_colours_and_labels_in_class_order() -> None:
    legend = figure.legend_markdown()
    lines = legend.splitlines()
    assert lines[0] == "| Colour | Path loss, dB |"
    assert lines[2] == "| `#fde725` | below 60 |"
    assert lines[-2] == "| `#440154` | 110 or more |"
    assert lines[-1] == "| none (transparent) | no value |"
    assert "\u2013" not in legend and "\u2014" not in legend


def test_web_readme_carries_the_legend_table() -> None:
    assert figure.legend_markdown() in (REPO / "web" / "README.md").read_text(encoding="utf-8")


def test_upscale_repeats_each_cell_as_a_block() -> None:
    indices = np.array([[1, 2], [3, 0]], dtype=np.uint8)
    scaled = figure.upscale(indices, 3)
    assert scaled.shape == (6, 6)
    assert scaled.dtype == np.uint8
    assert (scaled[:3, :3] == 1).all() and (scaled[3:, 3:] == 0).all()


@pytest.mark.parametrize(("width", "expected"), [(110, 6), (600, 1), (700, 1), (1, 600), (99, 7)])
def test_scale_factor_reaches_the_target_width(width: int, expected: int) -> None:
    assert figure.scale_factor(width) == expected


def test_upscale_refuses_a_factor_below_one() -> None:
    with pytest.raises(ValueError, match="factor"):
        figure.upscale(np.zeros((2, 2), dtype=np.uint8), 0)


def write_layer(path: Path, marker: str | None) -> Path:
    pq.write_table(pa.table({"site_id": ["a"]}), path)
    if marker is not None:
        stamp_positions(path, marker)
    return path


def write_coverage(path: Path, values: np.ndarray) -> Path:
    profile = {
        "driver": "GTiff",
        "width": values.shape[1],
        "height": values.shape[0],
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:26915",
        "transform": from_origin(725_000.0, 4_285_040.0, 10.0, 10.0),
        "nodata": -9999.0,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(np.where(np.isnan(values), -9999.0, values).astype(np.float32), 1)
    return path


def write_run(tmp_path: Path, sites_marker: str | None, receivers_marker: str | None) -> Path:
    """A config folder and a complete run folder; returns the coverage file."""
    config_folder = tmp_path / "config"
    config_folder.mkdir()
    sites = write_layer(config_folder / "sites.parquet", sites_marker)
    receivers = write_layer(config_folder / "receivers.parquet", receivers_marker)
    config = config_folder / "run.yaml"
    config.write_text("tx_sites: sites.parquet\nrx_points: receivers.parquet\n", encoding="utf-8")
    run = tmp_path / "runs" / "fixture"
    run.mkdir(parents=True)
    values = np.array(
        [[np.nan, 55.0, 65.0, 75.0], [85.0, 95.0, 105.0, 115.0]] * 2, dtype=np.float64
    )
    coverage = write_coverage(run / COVERAGE_NAME, values)
    manifest = {
        "config_sha256": sha256(config),
        "inputs": [
            {"path": "sites.parquet", "sha256": sha256(sites)},
            {"path": "receivers.parquet", "sha256": sha256(receivers)},
        ],
        "artifacts": [{"path": COVERAGE_NAME, "sha256": sha256(coverage)}],
    }
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return coverage


def config_of(tmp_path: Path) -> Path:
    return tmp_path / "config" / "run.yaml"


def test_guard_admits_a_run_whose_point_layers_are_synthetic(tmp_path: Path) -> None:
    coverage = write_run(tmp_path, "synthetic", "synthetic")
    figure.check_synthetic_run(coverage, config_of(tmp_path))


@pytest.mark.parametrize(("sites", "receivers"), [("real", "synthetic"), ("synthetic", "real")])
def test_guard_refuses_a_layer_marked_real(tmp_path: Path, sites: str, receivers: str) -> None:
    coverage = write_run(tmp_path, sites, receivers)
    with pytest.raises(figure.FigureRefusedError, match="synthetic"):
        figure.check_synthetic_run(coverage, config_of(tmp_path))


def test_guard_refuses_a_layer_without_a_marker(tmp_path: Path) -> None:
    coverage = write_run(tmp_path, "synthetic", None)
    with pytest.raises(figure.FigureRefusedError, match="synthetic"):
        figure.check_synthetic_run(coverage, config_of(tmp_path))


def test_guard_refuses_a_run_without_a_manifest(tmp_path: Path) -> None:
    coverage = write_run(tmp_path, "synthetic", "synthetic")
    (coverage.parent / "manifest.json").unlink()
    with pytest.raises(figure.FigureRefusedError, match="manifest"):
        figure.check_synthetic_run(coverage, config_of(tmp_path))


def test_guard_refuses_a_config_the_run_did_not_use(tmp_path: Path) -> None:
    coverage = write_run(tmp_path, "synthetic", "synthetic")
    config = config_of(tmp_path)
    config.write_text(config.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    with pytest.raises(figure.FigureRefusedError, match="config"):
        figure.check_synthetic_run(coverage, config)


def test_guard_refuses_a_layer_changed_after_the_run(tmp_path: Path) -> None:
    coverage = write_run(tmp_path, "synthetic", "synthetic")
    receivers = tmp_path / "config" / "receivers.parquet"
    pq.write_table(pa.table({"site_id": ["a", "b"]}), receivers)
    stamp_positions(receivers, "synthetic")
    with pytest.raises(figure.FigureRefusedError, match="receivers.parquet"):
        figure.check_synthetic_run(coverage, config_of(tmp_path))


def test_guard_refuses_a_coverage_file_the_manifest_does_not_list(tmp_path: Path) -> None:
    coverage = write_run(tmp_path, "synthetic", "synthetic")
    other = write_coverage(coverage.with_name("coverage_site-b_fspl.tif"), np.ones((2, 2)))
    with pytest.raises(figure.FigureRefusedError, match="coverage_site-b_fspl.tif"):
        figure.check_synthetic_run(other, config_of(tmp_path))


def test_guard_refuses_a_coverage_file_changed_after_the_run(tmp_path: Path) -> None:
    coverage = write_run(tmp_path, "synthetic", "synthetic")
    write_coverage(coverage, np.ones((2, 2)))
    with pytest.raises(figure.FigureRefusedError, match=COVERAGE_NAME):
        figure.check_synthetic_run(coverage, config_of(tmp_path))


def test_read_coverage_turns_nodata_into_nan(tmp_path: Path) -> None:
    coverage = write_run(tmp_path, "synthetic", "synthetic")
    values = figure.read_coverage(coverage)
    assert values.shape == (4, 4)
    assert np.isnan(values[0, 0]) and values[0, 1] == 55.0


@pytest.mark.filterwarnings("ignore::rasterio.errors.NotGeoreferencedWarning")
def test_main_writes_a_paletted_png_with_transparent_nodata(tmp_path: Path) -> None:
    coverage = write_run(tmp_path, "synthetic", "synthetic")
    out = tmp_path / "figure" / "coverage.png"
    assert figure.main([str(coverage), str(config_of(tmp_path)), str(out)]) == 0
    with rasterio.open(out) as png:
        assert png.count == 1 and png.width == 4 * figure.scale_factor(4)
        assert png.colormap(1)[0] == (0, 0, 0, 0)
    assert sorted(path.name for path in out.parent.iterdir()) == ["coverage.png"]


def test_main_refuses_a_real_run_and_writes_nothing(tmp_path: Path) -> None:
    coverage = write_run(tmp_path, "real", "synthetic")
    out = tmp_path / "figure.png"
    assert figure.main([str(coverage), str(config_of(tmp_path)), str(out)]) == 2
    assert not out.exists()
