"""engine.prepare's rule for a real layer: every row carries its box.

Casey, PR #73: "engine.prepare refuses a layer that mixes rows with edges and
rows without, with a test, so box_form is true for every row." A real node
always has a position box, so in a real layer with the edge columns a row
with all four edges null would be drawn as an exact point. The made-up grid
rows prepare adds to `rx_points.parquet` are exact points by design and are
never read through this rule. A real square row with half-width 0 is
refused for the same reason (Casey, issue #55 comment 7024307502, item 2,
Q3 (a)). Every box here is made up.
"""

from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point
from test_engine_points import EDGES, write_edge_layer
from test_terrain_edge_boxes import TX_CELL

from engine.errors import ConfigError
from engine.points import read_points
from engine.prepare.command import require_real_boxes

NO_EDGES: tuple[None, None, None, None] = (None, None, None, None)


def test_a_real_edge_layer_with_every_row_boxed_is_accepted(tmp_path: Path) -> None:
    path = write_edge_layer(tmp_path / "rx.parquet", [("a", TX_CELL.centre_xy_m, EDGES)])
    require_real_boxes(path, read_points(path, "rx_id"))


@pytest.mark.parametrize(
    "rows",
    [
        [("a", TX_CELL.centre_xy_m, EDGES), ("b", (725_400.0, 4_285_300.0), NO_EDGES)],
        [("b", (725_400.0, 4_285_300.0), NO_EDGES)],
    ],
    ids=["rows with and without edges", "no row with edges"],
)
def test_a_real_edge_layer_with_a_row_without_edges_is_refused(
    tmp_path: Path, rows: list[tuple[str, tuple[float, float], tuple[int | None, ...]]]
) -> None:
    path = write_edge_layer(tmp_path / "rx.parquet", rows)
    with pytest.raises(ConfigError, match="row b has no box edges"):
        require_real_boxes(path, read_points(path, "rx_id"))


def square_layer(path: Path, half_widths_m: list[float]) -> Path:
    ids = [chr(ord("a") + index) for index in range(len(half_widths_m))]
    gpd.GeoDataFrame(
        {"rx_id": ids, "box_half_width_m": half_widths_m},
        geometry=[Point(725_400.0 + 100.0 * index, 4_285_300.0) for index in range(len(ids))],
        crs="EPSG:26915",
    ).to_parquet(path, index=False)
    return path


def test_a_real_square_layer_with_every_row_boxed_is_accepted(tmp_path: Path) -> None:
    path = square_layer(tmp_path / "square.parquet", [182.4, 91.2])
    require_real_boxes(path, read_points(path, "rx_id"))


def test_a_real_square_row_with_half_width_0_is_refused(tmp_path: Path) -> None:
    # Casey, issue #55 comment 7024307502, item 2, Q3 (a): a real row with
    # half-width 0 would be drawn as an exact point while box_form says square.
    path = square_layer(tmp_path / "square.parquet", [182.4, 0.0])
    with pytest.raises(ConfigError, match="row b has a box half-width of 0"):
        require_real_boxes(path, read_points(path, "rx_id"))
