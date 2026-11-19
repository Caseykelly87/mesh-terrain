"""Which point layers may reach the engine: synthetic ones, and real ones a `prepared.json` lists.

Casey, issue #55 comment 7018724580: engine.prepare's checks bind only if
"a hand-built layer cannot reach the engine". A layer marked `synthetic`
passes. A layer marked `real` passes only when the `prepared.json` that
engine.prepare writes beside it lists the layer's name with its sha256, so a
real layer copied away from that file, or edited after it, is refused, and
so is a layer without the marker. It stops accidents, not deliberate bypass:
a hand-written `prepared.json` in prepare's format, or a real layer
re-marked `synthetic`, still passes (Casey, issue #55 comment 7024307502,
item 2, Q1 (a)). Every layer here is made up.
"""

import json
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point

from engine.errors import ConfigError
from engine.positions_marker import REAL, SYNTHETIC, stamp_positions
from engine.prepared import STAMP_FILE, require_admissible_layer, write_prepared_stamp
from pipeline.common.snapshot import sha256_file


def layer(path: Path, marker: str | None, x_m: float = 725_400.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(
        {"rx_id": ["a"]}, geometry=[Point(x_m, 4_285_300.0)], crs="EPSG:26915"
    ).to_parquet(path, index=False)
    if marker is not None:
        stamp_positions(path, marker)
    return path


def test_a_synthetic_layer_is_admitted_anywhere(tmp_path: Path) -> None:
    require_admissible_layer(layer(tmp_path / "rx.parquet", SYNTHETIC))


def test_an_unmarked_layer_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="mesh_terrain:positions"):
        require_admissible_layer(layer(tmp_path / "rx.parquet", None))


def test_a_real_layer_engine_prepare_wrote_is_admitted(tmp_path: Path) -> None:
    sites = layer(tmp_path / "tx_sites.parquet", REAL)
    receivers = layer(tmp_path / "rx_points.parquet", REAL, x_m=725_500.0)
    stamp = write_prepared_stamp(tmp_path, [sites.name, receivers.name], tile="x72y429")

    assert stamp == tmp_path / STAMP_FILE
    written = json.loads(stamp.read_text(encoding="utf-8"))
    assert written["written_by"] == "engine.prepare"
    assert written["tile"] == "x72y429"
    assert written["layers"] == {
        sites.name: sha256_file(sites),
        receivers.name: sha256_file(receivers),
    }
    require_admissible_layer(sites)
    require_admissible_layer(receivers)


def test_a_real_layer_without_the_stamp_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="engine.prepare"):
        require_admissible_layer(layer(tmp_path / "rx_points.parquet", REAL))


def test_a_real_layer_copied_away_from_its_stamp_is_refused(tmp_path: Path) -> None:
    receivers = layer(tmp_path / "prepared" / "rx_points.parquet", REAL)
    write_prepared_stamp(receivers.parent, [receivers.name], tile="x72y429")
    copy = tmp_path / "elsewhere" / "rx_points.parquet"
    copy.parent.mkdir()
    copy.write_bytes(receivers.read_bytes())

    with pytest.raises(ConfigError, match="engine.prepare"):
        require_admissible_layer(copy)


def test_a_real_layer_edited_after_prepare_is_refused(tmp_path: Path) -> None:
    receivers = layer(tmp_path / "rx_points.parquet", REAL)
    write_prepared_stamp(tmp_path, [receivers.name], tile="x72y429")
    layer(receivers, REAL, x_m=725_401.0)

    with pytest.raises(ConfigError, match="sha256"):
        require_admissible_layer(receivers)


def test_a_real_layer_the_stamp_does_not_list_is_refused(tmp_path: Path) -> None:
    sites = layer(tmp_path / "tx_sites.parquet", REAL)
    write_prepared_stamp(tmp_path, [sites.name], tile="x72y429")
    other = layer(tmp_path / "hand_built.parquet", REAL)

    with pytest.raises(ConfigError, match="hand_built.parquet"):
        require_admissible_layer(other)


def test_an_unreadable_stamp_is_refused(tmp_path: Path) -> None:
    receivers = layer(tmp_path / "rx_points.parquet", REAL)
    (tmp_path / STAMP_FILE).write_text("not json", encoding="utf-8")

    with pytest.raises(ConfigError, match=STAMP_FILE):
        require_admissible_layer(receivers)


def test_a_marker_other_than_real_or_synthetic_is_refused(tmp_path: Path) -> None:
    import pyarrow.parquet as pq

    path = layer(tmp_path / "rx.parquet", None)
    table = pq.read_table(path)
    metadata = {**(table.schema.metadata or {}), b"mesh_terrain:positions": b"maybe"}
    pq.write_table(table.replace_schema_metadata(metadata), path)

    with pytest.raises(ConfigError, match="maybe"):
        require_admissible_layer(path)


def test_a_stamp_another_writer_wrote_is_refused(tmp_path: Path) -> None:
    receivers = layer(tmp_path / "rx_points.parquet", REAL)
    stamp = {"written_by": "other", "layers": {receivers.name: sha256_file(receivers)}}
    (tmp_path / STAMP_FILE).write_text(json.dumps(stamp), encoding="utf-8")

    with pytest.raises(ConfigError, match="not written by engine.prepare"):
        require_admissible_layer(receivers)
