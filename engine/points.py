"""Site and receiver point layers, read once and converted to EPSG:26915 at the boundary.

A layer is GeoParquet with an id column (`site_id` or `rx_id`) and point
geometry in EPSG:4326 or EPSG:26915 (`docs/INTERFACE.md`). An optional
`box_half_width_m` column gives each point's position box in metres; without
it every point is exact. Sites and receivers are taken as given, boxes
included: the engine never improves a position. Nothing here computes on
degrees; the one conversion is pyproj's, through geopandas.
"""

import math
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd

from engine.errors import ConfigError
from terrain.draws import EndpointBox

ACCEPTED_EPSG = (4326, 26915)
BOX_COLUMN = "box_half_width_m"


@dataclass(frozen=True, kw_only=True)
class Endpoint:
    """One site or receiver.

    Attributes:
        endpoint_id: The layer's `site_id` or `rx_id`.
        box: Position box in EPSG:26915 metres; half-width 0 for an exact point.
    """

    endpoint_id: str
    box: EndpointBox


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
            that is not a point, or a box half-width that is negative or not a
            number.
    """
    try:
        layer = gpd.read_parquet(path)
    except (OSError, ValueError) as err:
        raise ConfigError(f"{path.name} is not a readable GeoParquet layer: {err}") from err
    _check_layer(layer, path, id_column)
    projected = layer.to_crs(epsg=26915)
    half_widths = _half_widths(projected, path)
    return [
        Endpoint(
            endpoint_id=str(row_id),
            box=EndpointBox(centre_xy_m=(float(point.x), float(point.y)), half_width_m=half),
        )
        for row_id, point, half in zip(
            projected[id_column], projected.geometry, half_widths, strict=True
        )
    ]


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
