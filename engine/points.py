"""Site and receiver point layers, read once and converted to EPSG:26915 at the boundary.

A layer is GeoParquet with an id column (`site_id` or `rx_id`) and point
geometry in EPSG:4326 or EPSG:26915 (`docs/INTERFACE.md`). It gives each
point's position box in one of two forms, or none, never both:

- `box_half_width_m`: a square in EPSG:26915 with sides along the grid axes,
  0 for an exact point.
- `box_south_i`, `box_north_i`, `box_west_i`, `box_east_i`: whole numbers of
  1e-7 degrees, the half-open edges of the `lnk_02` box (ADR 0008). The point
  is the box centre, the midpoint of the edges converted to EPSG:26915; a row
  more than 1 mm from it is refused. A row with all four null is an exact
  point.

Without either form every point is exact. Sites and receivers are taken as
given, boxes included: the engine never improves a position. The only
conversions are pyproj's, through geopandas and `terrain.boxes`.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import geopandas as gpd
import pyarrow as pa
import pyarrow.parquet as pq

from engine.errors import ConfigError
from terrain.boxes import EdgeBox, EndpointBox, PositionBox

ACCEPTED_EPSG = (4326, 26915)
BOX_COLUMN = "box_half_width_m"
EDGE_COLUMNS = ("box_south_i", "box_north_i", "box_west_i", "box_east_i")
CENTRE_TOLERANCE_M = 0.001
"""How far a row's point may lie from its edge box's centre, metres."""

BoxForm = Literal["rectangle", "square", "point"]


@dataclass(frozen=True, kw_only=True)
class Endpoint:
    """One site or receiver.

    Attributes:
        endpoint_id: The layer's `site_id` or `rx_id`.
        box: Position box; a square of half-width 0 for an exact point.
    """

    endpoint_id: str
    box: PositionBox


def read_points(path: Path, id_column: str) -> list[Endpoint]:
    """Read a point layer into endpoints in EPSG:26915, in file order.

    Args:
        path: GeoParquet file.
        id_column: `site_id` or `rx_id`.

    Returns:
        One endpoint per row.

    Raises:
        ConfigError: for a file that is not GeoParquet, a missing or
            non-unique id, a CRS other than EPSG:4326 or EPSG:26915, a geometry
            that is not a point, a box half-width that is negative or not a
            number, both box forms or only some edge columns, an edge column
            that is not integer, a row with only some of its edges, edges out
            of order or range, or a point more than 1 mm from its box centre.
    """
    try:
        layer = gpd.read_parquet(path)
    except (OSError, ValueError) as err:
        raise ConfigError(f"{path.name} is not a readable GeoParquet layer: {err}") from err
    _check_layer(layer, path, id_column)
    projected = layer.to_crs(epsg=26915)
    ids = [str(row_id) for row_id in projected[id_column]]
    points = [(float(point.x), float(point.y)) for point in projected.geometry]
    if any(column in layer.columns for column in EDGE_COLUMNS):
        boxes = _edge_boxes(path, ids, points, set(layer.columns))
    else:
        boxes = [
            EndpointBox(centre_xy_m=point, half_width_m=half)
            for point, half in zip(points, _half_widths(projected, path), strict=True)
        ]
    return [Endpoint(endpoint_id=i, box=box) for i, box in zip(ids, boxes, strict=True)]


def box_form(endpoints: Sequence[Endpoint]) -> BoxForm:
    """What the draws of a layer used, for the manifest (Casey, PR #73, Q2 (b)).

    Args:
        endpoints: One layer's endpoints, as `read_points` returns them.

    Returns:
        `rectangle` when a row has `lnk_02` edges, `square` when a row has a
        half-width above 0, `point` when every row is an exact point.
    """
    if any(isinstance(e.box, EdgeBox) for e in endpoints):
        return "rectangle"
    if any(e.box.has_area for e in endpoints):
        return "square"
    return "point"


def _check_layer(layer: gpd.GeoDataFrame, path: Path, id_column: str) -> None:
    if id_column not in layer.columns:
        raise ConfigError(f"{path.name} has no {id_column} column")
    ids = layer[id_column]
    if ids.isna().any() or not all(isinstance(value, str) for value in ids):
        raise ConfigError(f"{path.name}: every {id_column} must be text")
    if ids.duplicated().any():
        raise ConfigError(f"{path.name}: {id_column} values must be unique")
    if layer.crs is None or layer.crs.to_epsg() not in ACCEPTED_EPSG:
        raise ConfigError(f"{path.name} must be in EPSG:4326 or EPSG:26915, got {layer.crs}")
    if len(layer) == 0 or not (layer.geom_type == "Point").all() or layer.geometry.is_empty.any():
        raise ConfigError(f"{path.name} must hold points, at least one")


def _half_widths(layer: gpd.GeoDataFrame, path: Path) -> list[float]:
    if BOX_COLUMN not in layer.columns:
        return [0.0] * len(layer)
    values = [float(value) for value in layer[BOX_COLUMN]]
    if not all(math.isfinite(value) and value >= 0.0 for value in values):
        raise ConfigError(f"{path.name}: {BOX_COLUMN} must be a finite number of at least 0")
    return values


def _edge_boxes(
    path: Path, ids: list[str], points: list[tuple[float, float]], columns: set[str]
) -> list[PositionBox]:
    """Each row's edge box, or an exact point where all four edges are null."""
    if BOX_COLUMN in columns:
        raise ConfigError(f"{path.name} carries both {BOX_COLUMN} and box edges; give one form")
    missing = [name for name in EDGE_COLUMNS if name not in columns]
    if missing:
        raise ConfigError(f"{path.name} has some box edge columns but not {', '.join(missing)}")
    table = pq.read_table(path, columns=list(EDGE_COLUMNS))
    for field in table.schema:
        if not pa.types.is_integer(field.type):
            raise ConfigError(f"{path.name}: {field.name} must be an integer column")
    rows = zip(*(table.column(name).to_pylist() for name in EDGE_COLUMNS), strict=True)
    return [
        _edge_box(path, row_id, point, edges)
        for row_id, point, edges in zip(ids, points, rows, strict=True)
    ]


def _edge_box(
    path: Path, row_id: str, point: tuple[float, float], edges: tuple[int | None, ...]
) -> PositionBox:
    if all(edge is None for edge in edges):
        return EndpointBox(centre_xy_m=point, half_width_m=0.0)
    if any(edge is None for edge in edges):
        raise ConfigError(f"{path.name}: row {row_id} has some of its four edges null")
    south_i, north_i, west_i, east_i = (int(edge) for edge in edges if edge is not None)
    try:
        box = EdgeBox(south_i=south_i, north_i=north_i, west_i=west_i, east_i=east_i)
    except ValueError as err:
        raise ConfigError(f"{path.name}: row {row_id}: {err}") from err
    if math.dist(point, box.centre_xy_m) > CENTRE_TOLERANCE_M:
        raise ConfigError(
            f"{path.name}: row {row_id} lies more than 1 mm from its box centre in EPSG:26915"
        )
    return box
