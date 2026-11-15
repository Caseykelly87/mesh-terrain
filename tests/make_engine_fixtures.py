"""Write the engine's committed synthetic fixtures; run once, commit the output.

Everything here is made up: no real position, lidar, canopy or OpenStreetMap
data. The 1 km sub-tile lies inside the USGS 1 m tile x72y429 (easting 725 000
to 726 000, northing 4 285 000 to 4 286 000, EPSG:26915) so that P.1812 reads a
St. Louis latitude, but its heights are functions of position:

- DEM: rolling ground near 150 m with a north-south ridge 20 m high through
  the middle, and a 10 m square without a value at (725 600, 4 285 600), so
  paths across it are refused as `dem_gap`.
- DSM: the DEM plus buildings (a downtown block in the north-east, houses in
  the south-west), 2 m over every tree (the leaf-off lidar sees bare
  branches), and a pond without a value that the clutter rule fills with 0.
- Canopy: the same trees at their full height, in EPSG:3857 at the canopy
  maps' 1.1943 m nominal pixel, uint8 metres with nodata 255, as two tiles,
  the eastern one with a 30 by 30 pixel gap without a value.
- Buildings: the DSM's building footprints as an OpenStreetMap-like layer.
- Points: two sites (EPSG:4326) and 500 receivers (EPSG:26915), 50 of them
  with a 20 m position box; 100 synthetic observations.

Run from the repository root:
`uv run --frozen python tests/make_engine_fixtures.py`. It refuses to overwrite
a fixture snapshot that exists, as every snapshot writer does.
"""

import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import numpy.typing as npt
import pandas as pd
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import Point, box

from pipeline.common.snapshot import finish_snapshot, start_snapshot, write_manifest

REPO = Path(__file__).resolve().parents[1]
SNAPSHOTS = REPO / "tests" / "fixtures" / "snapshots"
POINTS = REPO / "engine" / "fixtures"
WEST_M, SOUTH_M, SIZE_M = 725_000.0, 4_285_000.0, 1000
CANOPY_PIXEL = 1.1943
CANOPY_NODATA = 255
FloatGrid = npt.NDArray[np.float64]
TO_3857 = Transformer.from_crs("EPSG:26915", "EPSG:3857", always_xy=True)
TO_26915 = Transformer.from_crs("EPSG:3857", "EPSG:26915", always_xy=True)


def pixel_centres() -> tuple[FloatGrid, FloatGrid]:
    cols = np.arange(SIZE_M) + 0.5
    x_m, y_m = np.meshgrid(WEST_M + cols, SOUTH_M + SIZE_M - cols)
    return x_m, y_m


def ground_m(x_m: FloatGrid, y_m: FloatGrid) -> FloatGrid:
    u, v = (x_m - WEST_M) / SIZE_M, (y_m - SOUTH_M) / SIZE_M
    rolling = 10.0 * np.sin(2.6 * math.pi * u) * np.cos(1.8 * math.pi * v)
    ridge = 20.0 * np.exp(-(((u - 0.5 - 0.2 * (v - 0.5)) / 0.04) ** 2))
    return np.round((150.0 + rolling + ridge) * 8.0) / 8.0


def buildings() -> list[tuple[float, float, float, float, float]]:
    """(west, south, east, north, height) in EPSG:26915 metres."""
    rng = np.random.default_rng(429)
    blocks = [
        (x, y, x + 40.0, y + 40.0, float(rng.integers(10, 31)))
        for x in np.arange(725_600.0, 725_950.0, 50.0)
        for y in np.arange(4_285_650.0, 4_285_950.0, 50.0)
    ]
    houses = [
        (x, y, x + 10.0, y + 12.0, 7.0)
        for x in np.arange(725_100.0, 725_450.0, 30.0)
        for y in np.arange(4_285_100.0, 4_285_400.0, 30.0)
    ]
    return blocks + houses


def trees(x_m: FloatGrid, y_m: FloatGrid) -> FloatGrid:
    rng = np.random.default_rng(72)
    centres = rng.uniform([725_050.0, 4_285_050.0], [725_550.0, 4_285_600.0], size=(40, 2))
    height = np.zeros_like(x_m)
    for cx, cy in centres:
        crown = 16.0 * np.exp(-((x_m - cx) ** 2 + (y_m - cy) ** 2) / (2 * 8.0**2))
        height = np.maximum(height, np.where(crown > 2.0, crown, 0.0))
    return height


def write_raster(
    path: Path, data: npt.NDArray[np.generic], crs: str, transform: object, nodata: float
) -> None:
    rows, cols = data.shape
    profile = {
        "driver": "GTiff",
        "height": rows,
        "width": cols,
        "count": 1,
        "dtype": data.dtype.name,
        "crs": crs,
        "transform": transform,
        "nodata": nodata,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
        "compress": "deflate",
    }
    if data.dtype.kind == "f":
        profile["predictor"] = 3
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)


def surface_layers() -> tuple[npt.NDArray[np.float32], npt.NDArray[np.float32]]:
    x_m, y_m = pixel_centres()
    dem = ground_m(x_m, y_m)
    dsm = dem.copy()
    for west, south, east, north, height in buildings():
        inside = (x_m > west) & (x_m < east) & (y_m > south) & (y_m < north)
        dsm[inside] = dem[inside] + height
    dsm = np.where(trees(x_m, y_m) > 3.0, np.maximum(dsm, dem + 2.0), dsm)
    pond = ((x_m - 725_300.0) / 20.0) ** 2 + ((y_m - 4_285_750.0) / 12.5) ** 2 < 1.0
    dsm[pond] = -999999.0
    hole = (np.abs(x_m - 725_600.0) < 5.0) & (np.abs(y_m - 4_285_600.0) < 5.0)
    dem[hole] = -999999.0
    dsm[hole] = -999999.0
    return dem.astype(np.float32), dsm.astype(np.float32)


def canopy_tiles() -> list[tuple[npt.NDArray[np.uint8], object]]:
    west, south = TO_3857.transform(WEST_M - 60.0, SOUTH_M - 60.0)
    east, north = TO_3857.transform(WEST_M + SIZE_M + 60.0, SOUTH_M + SIZE_M + 60.0)
    width = math.ceil((east - west) / CANOPY_PIXEL / 2.0)
    height = math.ceil((north - south) / CANOPY_PIXEL)
    tiles = []
    for index in range(2):
        origin_x = west + index * width * CANOPY_PIXEL
        cols, rows = np.meshgrid(np.arange(width) + 0.5, np.arange(height) + 0.5)
        x_m, y_m = TO_26915.transform(origin_x + cols * CANOPY_PIXEL, north - rows * CANOPY_PIXEL)
        canopy = np.round(trees(np.asarray(x_m), np.asarray(y_m))).astype(np.uint8)
        if index == 1:
            canopy[300:330, 200:230] = CANOPY_NODATA
        tiles.append((canopy, from_origin(origin_x, north, CANOPY_PIXEL, CANOPY_PIXEL)))
    return tiles


def write_snapshot(snapshot_id: str, files: dict[str, object], readme: str) -> None:
    partial = start_snapshot(SNAPSHOTS, snapshot_id)
    for name, writer in files.items():
        writer(partial / name)  # type: ignore[operator]
    (partial / "README.md").write_text(readme, encoding="utf-8", newline="\n")
    metadata = {
        "snapshot_id": snapshot_id,
        "capture_date": "2026-11-15",
        "source_url": "none: made-up values written by tests/make_engine_fixtures.py",
        "licence": "MIT, as the repository; no third-party data",
        "seeds": {},
        "synthetic": True,
    }
    write_manifest(partial, metadata)
    finish_snapshot(partial)


def write_terrain_snapshots() -> None:
    dem, dsm = surface_layers()
    transform = from_origin(WEST_M, SOUTH_M + SIZE_M, 1.0, 1.0)
    note = (
        "Synthetic 1 km sub-tile of x72y429 (easting 725000 to 726000, northing 4285000 to "
        "4286000, EPSG:26915, 1 m pixels) for the engine fixture run. Made-up heights, no USGS "
        "data; see tests/make_engine_fixtures.py.\n"
    )
    write_snapshot(
        "dem-1m-synthetic-subtile",
        {"dem.tif": lambda p: write_raster(p, dem, "EPSG:26915", transform, -999999.0)},
        f"# dem-1m-synthetic-subtile\n\n{note}",
    )
    write_snapshot(
        "dsm-synthetic-subtile",
        {"dsm.tif": lambda p: write_raster(p, dsm, "EPSG:26915", transform, -999999.0)},
        f"# dsm-synthetic-subtile\n\n{note}",
    )
    (west, west_t), (east, east_t) = canopy_tiles()
    write_snapshot(
        "canopy-synthetic-subtile",
        {
            "canopy_west.tif": lambda p: write_raster(p, west, "EPSG:3857", west_t, CANOPY_NODATA),
            "canopy_east.tif": lambda p: write_raster(p, east, "EPSG:3857", east_t, CANOPY_NODATA),
        },
        "# canopy-synthetic-subtile\n\nTwo made-up canopy height tiles in EPSG:3857 at the "
        "canopy maps' 1.1943 m nominal pixel, uint8 metres, nodata 255; the eastern tile has "
        "a 30 by 30 pixel gap without a value. No Meta or WRI data.\n",
    )


def write_buildings_snapshot() -> None:
    footprints = gpd.GeoDataFrame(
        {"osm_id": np.arange(1, len(buildings()) + 1, dtype=np.int64)},
        geometry=[box(w, s, e, n) for w, s, e, n, _ in buildings()],
        crs="EPSG:26915",
    ).to_crs("EPSG:4326")
    write_snapshot(
        "osm-buildings-synthetic",
        {"buildings.parquet": lambda p: footprints.to_parquet(p, index=False)},
        "# osm-buildings-synthetic\n\nMade-up building footprints (EPSG:4326, `osm_id`, "
        "geometry) matching the synthetic DSM's buildings, in the layout the OpenStreetMap "
        "snapshot's `buildings.parquet` takes. No OpenStreetMap data.\n",
    )


def write_points() -> None:
    sites = gpd.GeoDataFrame(
        {"site_id": ["site-east", "site-west"], "box_half_width_m": [0.0, 0.0]},
        geometry=[Point(725_850.0, 4_285_500.0), Point(725_150.0, 4_285_520.0)],
        crs="EPSG:26915",
    ).to_crs("EPSG:4326")
    sites.to_parquet(POINTS / "two_sites.parquet", index=False)
    rng = np.random.default_rng(500)
    xy = np.round(rng.uniform([725_080.0, 4_285_080.0], [725_920.0, 4_285_920.0], (500, 2)), 2)
    half_widths = np.where(np.arange(500) % 10 == 0, 20.0, 0.0)
    receivers = gpd.GeoDataFrame(
        {"rx_id": [f"rx-{i:03d}" for i in range(500)], "box_half_width_m": half_widths},
        geometry=[Point(x, y) for x, y in xy],
        crs="EPSG:26915",
    )
    receivers.to_parquet(POINTS / "receivers_500.parquet", index=False)
    write_observations(sites.to_crs("EPSG:26915"), receivers, rng)


def write_observations(
    sites: gpd.GeoDataFrame, receivers: gpd.GeoDataFrame, rng: np.random.Generator
) -> None:
    picks = rng.choice(len(sites) * len(receivers), size=100, replace=False)
    rows = []
    for pick in sorted(int(p) for p in picks):
        site, receiver = sites.iloc[pick // len(receivers)], receivers.iloc[pick % len(receivers)]
        distance_m = max(site.geometry.distance(receiver.geometry), 1.0)
        loss_db = 125.0 + 30.0 * math.log10(distance_m / 1000.0) + rng.normal(0.0, 6.0)
        rows.append((site.site_id, receiver.rx_id, round(loss_db, 2)))
    observations = pd.DataFrame(rows, columns=["site_id", "rx_id", "observed_loss_db"])
    observations.to_parquet(POINTS / "observations.parquet", index=False)


if __name__ == "__main__":
    write_terrain_snapshots()
    write_buildings_snapshot()
    write_points()
