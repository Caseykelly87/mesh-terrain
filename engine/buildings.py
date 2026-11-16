"""Building footprints: the share near each endpoint and the building pixels of the 1 m grid.

The footprints come from the OpenStreetMap buildings snapshot's
`buildings.parquet` (polygons, any CRS geopandas can read, converted to
EPSG:26915 once). Overlapping footprints are merged first, so no area counts
twice. Two products, both from the same merged footprints:

- the building share within 100 m of a point: footprint area inside the
  circle divided by the circle's area, computed on polygons rather than
  pixels, so it is the same on the metro DEM and the 1 m layers (`ter_06`,
  ADR 0006 decision 7.1, which picks the Okumura-Hata variant from it);
- the building pixels of the 1 m grid: pixels whose centre lies inside a
  footprint (ADR 0006, addition C), for `tx_building_draws` and
  `rx_building_draws`.
"""

from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely
from rasterio.features import rasterize
from rasterio.io import DatasetReader
from shapely.geometry import Point
from shapely.strtree import STRtree

from terrain.draws import BuildingPixels
from terrain.profile import XY

SHARE_RADIUS_M = 100.0
"""Radius of the circle the building share is measured in, metres (`ter_05`, `ter_06`)."""

CIRCLE_QUADRANT_SEGMENTS = 64
"""Segments per quarter circle; the polygon's area is within 0.02 percent of pi r squared."""


@dataclass(frozen=True, eq=False)
class Footprints:
    """Merged building footprints in EPSG:26915.

    Attributes:
        parts: Non-overlapping polygons.
        tree: Spatial index over `parts`.
    """

    parts: tuple[shapely.Polygon, ...]
    tree: STRtree
    _shares: dict[tuple[XY, float], float] = field(default_factory=dict)

    @classmethod
    def from_frame(
        cls, frame: gpd.GeoDataFrame, window_m: tuple[float, float, float, float] | None = None
    ) -> "Footprints":
        """Merge a footprint layer's polygons, converted to EPSG:26915.

        Args:
            frame: Footprint polygons in any CRS.
            window_m: West, south, east and north in EPSG:26915 metres; only
                footprints that reach into it are kept, before they are
                repaired and merged, so a run reads the MSA-wide layer but
                merges only its own area. None keeps every footprint.
        """
        polygons = frame.to_crs(epsg=26915).geometry
        polygons = polygons[polygons.geom_type.isin(["Polygon", "MultiPolygon"])]
        if window_m is not None:
            polygons = polygons[polygons.intersects(shapely.box(*window_m))]
        merged = shapely.union_all(shapely.make_valid(polygons.to_numpy()))
        parts = tuple(p for p in shapely.get_parts(merged) if isinstance(p, shapely.Polygon))
        return cls(parts=parts, tree=STRtree(parts))

    @classmethod
    def from_parquet(
        cls, path: Path, window_m: tuple[float, float, float, float] | None = None
    ) -> "Footprints":
        """Read the snapshot's `buildings.parquet`, keeping footprints that reach `window_m`."""
        return cls.from_frame(gpd.read_parquet(path), window_m)

    def share_within(self, xy_m: XY, radius_m: float = SHARE_RADIUS_M) -> float:
        """Share of the circle around a point covered by footprints.

        Args:
            xy_m: Easting and northing in EPSG:26915, metres.
            radius_m: Circle radius in metres.

        Returns:
            A fraction from 0 to 1.
        """
        key = (xy_m, radius_m)
        if key not in self._shares:
            circle = Point(xy_m).buffer(radius_m, quad_segs=CIRCLE_QUADRANT_SEGMENTS)
            near = self.tree.query(circle)
            covered = sum(shapely.intersection(self.parts[i], circle).area for i in near)
            self._shares[key] = min(1.0, float(covered) / circle.area)
        return self._shares[key]

    def pixels(self, dem: DatasetReader) -> BuildingPixels:
        """Building pixels on a DEM's grid: pixels whose centre lies inside a footprint.

        Args:
            dem: The open 1 m DEM of the run's terrain build.

        Returns:
            The mask with the DEM's transform.
        """
        mask = np.zeros(dem.shape, dtype=np.bool_)
        if self.parts:
            burned = rasterize(
                ((part, 1) for part in self.parts),
                out_shape=dem.shape,
                transform=dem.transform,
                fill=0,
                dtype="uint8",
                all_touched=False,
            )
            mask = burned.astype(np.bool_)
        return BuildingPixels(mask=mask, transform=dem.transform)
