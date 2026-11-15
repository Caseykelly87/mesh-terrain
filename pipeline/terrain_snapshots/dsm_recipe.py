"""The DSM recipe (Casey, unit 5b (a) A1) as PDAL pipeline stages, and the window grid.

The DSM is the first-return lidar surface: roof tops and tree tops. Recipe A1:

- First returns only, `ReturnNumber == 1`, which includes single returns.
- Classes 7 (low noise) and 18 (high noise) and withheld points dropped;
  ground, water, ignored ground and bridge decks kept, since a bridge deck is
  a real obstacle.
- The highest kept point in each 1 m cell (writers.gdal `binmode`, so a cell
  sees only the points inside it), on the 1 m DEM tile's grid.
- One-cell gaps between scan lines filled from the neighbouring cells
  (`window_size` 1); larger gaps such as water stay empty. Casey approved the
  reading that "clutter 0 where the DSM has no value" (ADR 0006 decision 6) is
  for water, so filled scan-line gaps do not become 0 m holes in roofs.

Every number is EPSG:26915 metres. The EPT is stored in EPSG:3857 and
reprojected to EPSG:26915 with PROJ's network off (Casey, PR #34, decision A),
which gives the NAD83(2011) / UTM 15N numbers the lidar was delivered in.

A build is split into square windows so no PDAL run holds the whole tile in
memory. Each window's pipeline grids one extra cell on every side, so the gap
fill sees the neighbouring window's cells as it would in a single pass; the
command keeps only the window's own cells.
"""

from dataclasses import dataclass
from typing import Any

EPT_URL = "https://usgs-lidar-public.s3.amazonaws.com/USGS_LPC_MO_StLouis_2017_LAS_2018/ept.json"
WHERE = "ReturnNumber == 1 && Classification != 7 && Classification != 18 && Withheld == 0"
NODATA = -9999.0
HALO_CELLS = 1
CRS = "EPSG:26915"


@dataclass(frozen=True)
class Grid:
    """A north-up grid in EPSG:26915: south-west corner, size in cells, cell size in metres."""

    west: float
    south: float
    width: int
    height: int
    resolution: float = 1.0

    @property
    def north(self) -> float:
        return self.south + self.height * self.resolution

    @property
    def east(self) -> float:
        return self.west + self.width * self.resolution


@dataclass(frozen=True)
class Window:
    """A block of a grid, offsets in cells from the north-west corner."""

    name: str
    col_off: int
    row_off: int
    width: int
    height: int

    def bounds(self, grid: Grid) -> tuple[float, float, float, float]:
        """West, south, east, north of the window, EPSG:26915 metres."""
        west = grid.west + self.col_off * grid.resolution
        north = grid.north - self.row_off * grid.resolution
        return (
            west,
            north - self.height * grid.resolution,
            west + self.width * grid.resolution,
            north,
        )


def split(grid: Grid, per_side: int) -> list[Window]:
    """Split a grid into per_side by per_side equal windows, row by row from the north-west.

    Raises:
        ValueError: when the grid does not divide evenly.
    """
    if grid.width % per_side or grid.height % per_side:
        raise ValueError(f"{grid.width} by {grid.height} cells do not split into {per_side} a side")
    width, height = grid.width // per_side, grid.height // per_side
    return [
        Window(f"r{row}c{col}", col * width, row * height, width, height)
        for row in range(per_side)
        for col in range(per_side)
    ]


def pipeline_stages(grid: Grid, window: Window, ept_url: str) -> list[dict[str, Any]]:
    """The PDAL pipeline for one window: read the EPT, reproject, grid with recipe A1.

    The writer's grid is the window plus HALO_CELLS on every side; `origin_x`
    and `origin_y` are its south-west corner, which avoids the inclusive
    `bounds` of writers.gdal (a 100 m box at 1 m gives 101 cells).
    """
    west, south, east, north = window.bounds(grid)
    halo = HALO_CELLS * grid.resolution
    x0, y0, x1, y1 = west - halo, south - halo, east + halo, north + halo
    return [
        {
            "type": "readers.ept",
            "filename": ept_url,
            "bounds": f"([{x0}, {x1}], [{y0}, {y1}])/{CRS}",
        },
        {"type": "filters.reprojection", "out_srs": CRS},
        {
            "type": "writers.gdal",
            "filename": f"/work/{window.name}.tif",
            "where": WHERE,
            "binmode": True,
            "output_type": "max,count",
            "resolution": grid.resolution,
            "origin_x": x0,
            "origin_y": y0,
            "width": window.width + 2 * HALO_CELLS,
            "height": window.height + 2 * HALO_CELLS,
            "window_size": 1,
            "data_type": "float32",
            "nodata": NODATA,
            "override_srs": CRS,
            "gdalopts": "COMPRESS=DEFLATE,TILED=YES",
        },
    ]
