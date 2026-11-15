"""The DSM recipe (Casey, unit 5b (a) A1) as PDAL pipeline stages, and the window grid.

The DSM is the first-return lidar surface: roof tops and tree tops. Recipe A1:

- First returns only, `ReturnNumber == 1`, which includes single returns.
- Noise classes 7 and 18 dropped; ground, water, ignored ground and bridge
  decks kept, since a bridge deck is a real obstacle.
- The highest kept point in each 1 m cell (writers.gdal `binmode`, so a cell
  sees only the points inside it), on the 1 m DEM tile's grid.
- Gaps between scan lines filled (writers.gdal `window_size` 1): a cell is
  filled when one of the eight cells around it has a value.
  Casey approved the reading that "clutter 0 where the DSM has no value"
  (ADR 0006 decision 6) is for water, so filled scan-line gaps do not become
  0 m holes in roofs (PR #34 comment 6901088171).

Decided without Casey: the `Withheld == 0` term. The EPT schema has no
`Withheld` dimension by name; it has `ClassFlags`, the LAS 1.4 flag byte. PDAL
2.8.4 replaces `ClassFlags` with Synthetic, KeyPoint, Withheld and Overlap
(io/private/ept/EptInfo.cpp, addFlags), reads each laszip tile with LasReader
(io/private/ept/TileContents.cpp, readLaszip), and LasReader sets Withheld
from the withheld bit, `(flags >> 2) & 0x01` (io/LasReader.cpp). No point in
three 200 m probe boxes of x72y429 was withheld, and a 200 by 100 m box gave
the same 101 626 first returns with and without the term, so it changes
nothing here and drops any point the vendor flagged withheld.

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
    """A north-up EPSG:26915 grid: south-west corner and cell size in metres, size in cells."""

    west_m: float
    south_m: float
    width: int
    height: int
    resolution_m: float = 1.0

    @property
    def north_m(self) -> float:
        return self.south_m + self.height * self.resolution_m

    @property
    def east_m(self) -> float:
        return self.west_m + self.width * self.resolution_m


def grid_from_bounds(bounds: tuple[float, float, float, float], resolution_m: float) -> Grid:
    """The grid whose cells tile west, south, east, north exactly, EPSG:26915 metres.

    Raises:
        ValueError: when the extent is not a whole number of cells.
    """
    west, south, east, north = bounds
    width, height = (east - west) / resolution_m, (north - south) / resolution_m
    if not (width.is_integer() and height.is_integer()):
        raise ValueError(f"{bounds} is not a whole number of {resolution_m} m cells")
    return Grid(west, south, int(width), int(height), resolution_m)


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
        west = grid.west_m + self.col_off * grid.resolution_m
        north = grid.north_m - self.row_off * grid.resolution_m
        return (
            west,
            north - self.height * grid.resolution_m,
            west + self.width * grid.resolution_m,
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
    halo = HALO_CELLS * grid.resolution_m
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
            "resolution": grid.resolution_m,
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
