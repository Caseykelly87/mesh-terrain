"""The positions marker on point layers: file metadata saying whether any row is a real position.

The key `mesh_terrain:positions` in a Parquet file's key-value metadata is
`real` or `synthetic` (Casey, issue #44, item 4 (a)); a layer is `synthetic`
only when every row is made up. The engine admits a `synthetic` layer, or a
`real` one listed in the `prepared.json` beside it (`engine.prepared`), and the
export rule queued after the x72y429 run will record it.
"""

from pathlib import Path

import geopandas as gpd
import pyarrow.parquet as pq
import pytest
from shapely.geometry import Point

from engine.hashing import table_content_sha256
from engine.positions_marker import POSITIONS_KEY, positions_marker, stamp_positions

FIXTURES = Path(__file__).resolve().parents[1] / "engine" / "fixtures"


def write_layer(path: Path) -> Path:
    layer = gpd.GeoDataFrame(
        {"rx_id": ["a", "b"], "box_half_width_m": [0.0, 5.0]},
        geometry=[Point(725_000.0, 4_285_000.0), Point(725_100.0, 4_285_100.0)],
        crs="EPSG:26915",
    )
    layer.to_parquet(path, index=False)
    return path


@pytest.mark.parametrize("value", ["real", "synthetic"])
def test_the_marker_is_file_metadata_that_read_metadata_sees(tmp_path: Path, value: str) -> None:
    path = write_layer(tmp_path / "layer.parquet")
    stamp_positions(path, value)
    assert pq.read_metadata(path).metadata[POSITIONS_KEY.encode()] == value.encode()
    assert positions_marker(path) == value


def test_stamping_keeps_the_rows_and_the_geoparquet_metadata(tmp_path: Path) -> None:
    path = write_layer(tmp_path / "layer.parquet")
    before = table_content_sha256(path)
    stamp_positions(path, "real")
    assert table_content_sha256(path) == before
    layer = gpd.read_parquet(path)
    assert layer.crs.to_epsg() == 26915
    assert list(layer["rx_id"]) == ["a", "b"]


def test_an_unmarked_layer_reads_as_none(tmp_path: Path) -> None:
    assert positions_marker(write_layer(tmp_path / "layer.parquet")) is None


def test_only_real_or_synthetic_is_stamped(tmp_path: Path) -> None:
    path = write_layer(tmp_path / "layer.parquet")
    with pytest.raises(ValueError, match="real or synthetic"):
        stamp_positions(path, "made-up")


@pytest.mark.parametrize(
    "name", ["two_sites.parquet", "receivers_500.parquet", "observations.parquet"]
)
def test_the_committed_fixture_layers_are_synthetic(name: str) -> None:
    assert positions_marker(FIXTURES / name) == "synthetic"
