"""Point layers with a position box given by its four `lnk_02` edges (ADR 0008).

A layer gives each point's box as `box_half_width_m` or as the four integer
edges `box_south_i`, `box_north_i`, `box_west_i`, `box_east_i`, never both.
With edges, the point is the box centre and a row with all four null is an
exact point. Every box here is made up.
"""

from pathlib import Path

import geopandas as gpd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shapely.geometry import Point
from test_terrain_edge_boxes import TX_CELL

from engine.errors import ConfigError
from engine.points import EDGE_COLUMNS, box_form, layer_schema_version, read_points
from terrain.draws import EdgeBox, EndpointBox

INT64 = pa.int64()
EDGES = (TX_CELL.south_i, TX_CELL.north_i, TX_CELL.west_i, TX_CELL.east_i)


def write_edge_layer(
    path: Path,
    rows: list[tuple[str, tuple[float, float], tuple[int | None, ...]]],
    *,
    crs: str = "EPSG:26915",
    edge_type: pa.DataType = INT64,
) -> Path:
    """An `rx_id` layer with the four edges; geometry in EPSG:26915 unless crs says 4326."""
    frame = gpd.GeoDataFrame(
        {"rx_id": [r[0] for r in rows]},
        geometry=[Point(*r[1]) for r in rows],
        crs="EPSG:26915",
    ).to_crs(crs)
    frame.to_parquet(path, index=False)
    table = pq.read_table(path)
    for index, name in enumerate(EDGE_COLUMNS):
        values = [r[2][index] for r in rows]
        table = table.append_column(name, pa.array(values, type=edge_type))
    pq.write_table(table, path)
    return path


def test_edges_read_to_an_edge_box_in_either_crs(tmp_path: Path) -> None:
    rows = [("cell", TX_CELL.centre_xy_m, EDGES)]
    projected = read_points(write_edge_layer(tmp_path / "utm.parquet", rows), "rx_id")
    geographic = read_points(
        write_edge_layer(tmp_path / "geo.parquet", rows, crs="EPSG:4326"), "rx_id"
    )

    assert projected[0].box == TX_CELL
    assert geographic[0].box == TX_CELL
    assert box_form(projected) == "rectangle"


def test_a_row_without_edges_is_an_exact_point(tmp_path: Path) -> None:
    rows = [
        ("cell", TX_CELL.centre_xy_m, EDGES),
        ("grid", (725_400.0, 4_285_300.0), (None, None, None, None)),
    ]
    endpoints = read_points(write_edge_layer(tmp_path / "rx.parquet", rows), "rx_id")

    assert endpoints[1].box == EndpointBox(centre_xy_m=(725_400.0, 4_285_300.0), half_width_m=0.0)
    assert box_form(endpoints) == "rectangle"


def test_box_form_names_squares_and_points() -> None:
    from engine.points import Endpoint

    point = Endpoint(endpoint_id="p", box=EndpointBox(centre_xy_m=(0.0, 0.0), half_width_m=0.0))
    square = Endpoint(endpoint_id="s", box=EndpointBox(centre_xy_m=(0.0, 0.0), half_width_m=5.0))

    assert box_form([point]) == "point"
    assert box_form([point, square]) == "square"
    assert box_form([Endpoint(endpoint_id="c", box=TX_CELL)]) == "rectangle"


def test_a_centre_more_than_a_millimetre_off_is_refused(tmp_path: Path) -> None:
    x_m, y_m = TX_CELL.centre_xy_m
    near = write_edge_layer(tmp_path / "near.parquet", [("a", (x_m + 0.0005, y_m), EDGES)])
    far = write_edge_layer(tmp_path / "far.parquet", [("a", (x_m + 0.002, y_m), EDGES)])

    assert isinstance(read_points(near, "rx_id")[0].box, EdgeBox)
    with pytest.raises(ConfigError, match="box centre"):
        read_points(far, "rx_id")


def test_a_layer_with_both_box_forms_is_refused(tmp_path: Path) -> None:
    path = write_edge_layer(tmp_path / "both.parquet", [("a", TX_CELL.centre_xy_m, EDGES)])
    table = pq.read_table(path)
    pq.write_table(table.append_column("box_half_width_m", pa.array([5.0])), path)

    with pytest.raises(ConfigError, match="both"):
        read_points(path, "rx_id")


def test_a_layer_with_some_edge_columns_is_refused(tmp_path: Path) -> None:
    path = write_edge_layer(tmp_path / "three.parquet", [("a", TX_CELL.centre_xy_m, EDGES)])
    pq.write_table(pq.read_table(path).drop_columns(["box_east_i"]), path)

    with pytest.raises(ConfigError, match="box_east_i"):
        read_points(path, "rx_id")


@pytest.mark.parametrize(
    ("edges", "edge_type", "message"),
    [
        ((EDGES[0], EDGES[1], EDGES[2], None), pa.int64(), "some of its four edges"),
        (tuple(float(e) for e in EDGES), pa.float64(), "integer"),
        ((EDGES[1], EDGES[0], EDGES[2], EDGES[3]), pa.int64(), "south_i"),
        ((EDGES[0], EDGES[1], EDGES[3], EDGES[2]), pa.int64(), "west_i"),
        ((899_999_000, 900_000_001, EDGES[2], EDGES[3]), pa.int64(), "latitude"),
    ],
)
def test_bad_edges_are_refused(
    tmp_path: Path, edges: tuple[int | None, ...], edge_type: pa.DataType, message: str
) -> None:
    path = write_edge_layer(
        tmp_path / "bad.parquet", [("a", TX_CELL.centre_xy_m, edges)], edge_type=edge_type
    )
    with pytest.raises(ConfigError, match=message):
        read_points(path, "rx_id")


def test_a_layer_records_the_point_schema_its_columns_use(tmp_path: Path) -> None:
    rows = [("grid", (725_400.0, 4_285_300.0), (None, None, None, None))]
    with_edges = write_edge_layer(tmp_path / "edges.parquet", rows)
    square = tmp_path / "square.parquet"
    gpd.GeoDataFrame(
        {"rx_id": ["a"], "box_half_width_m": [5.0]},
        geometry=[Point(725_400.0, 4_285_300.0)],
        crs="EPSG:26915",
    ).to_parquet(square, index=False)

    assert layer_schema_version(with_edges) == "1.1.0"
    assert layer_schema_version(square) == "1.0.0"


@pytest.mark.parametrize(
    "edges",
    [
        (EDGES[1], EDGES[0], EDGES[2], EDGES[3]),
        (EDGES[0], EDGES[1], EDGES[2], None),
        (899_999_000, 900_000_001, EDGES[2], EDGES[3]),
    ],
    ids=["order", "some null", "range"],
)
def test_a_bad_edge_row_is_refused_without_its_edges_in_the_message(
    tmp_path: Path, edges: tuple[int | None, ...]
) -> None:
    path = write_edge_layer(tmp_path / "bad.parquet", [("node-a", TX_CELL.centre_xy_m, edges)])
    with pytest.raises(ConfigError) as raised:
        read_points(path, "rx_id")
    message = str(raised.value)
    assert "node-a" in message
    for value in edges:
        if value is not None:
            assert str(value) not in message
