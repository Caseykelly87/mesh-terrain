"""The 1 m DEM tile snapshot: one USGS 3DEP one-metre tile and its metadata file.

The tile is the bare-earth elevation model the 1 m profiles and the clutter
rule read (ADR 0006). The command checks the file's size, ETag and date
against the values measured in ADR 0006 spike run 7, downloads the tile and
its TNM metadata XML into `<id>.partial/`, then opens the tile header and
refuses it unless its CRS, pixel size and extent are the pinned ones. It counts
nodata pixels block by block, never reading the raster whole, so the engine run
report can state that the snapshot it used has no DEM gap.

Coordinates are EPSG:26915 metres, the label the file carries. The lidar it
was made from is NAD83(2011) / UTM 15N (EPSG:6344), whose numbers are the same
with PROJ's network off (Casey, PR #34); the manifest records both.
"""

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import rasterio

from pipeline.common.snapshot import SnapshotError
from pipeline.terrain_snapshots.common import build_snapshot, timed, today_utc, write_readme
from pipeline.terrain_snapshots.remote import Opener, PinnedObject, fetch_all, urlopen_opener

LICENCE = "public domain (USGS 3DEP)"
HORIZONTAL_DATUM = "NAD83(2011)"
VERTICAL_DATUM = "NAVD88 (EPSG:5703), GEOID12B"
_TNM = "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1m/Projects/MO_SaintLouis_2017/"
_PUBDATE = re.compile(rb"<pubdate>\s*(\d{4})(\d{2})(\d{2})\s*</pubdate>")


@dataclass(frozen=True)
class DemSource:
    """One 1 m DEM tile, its metadata file, and the grid it must have.

    Attributes:
        snapshot_id: The snapshot id the command writes.
        tile: The GeoTIFF's pin.
        metadata_xml: The TNM metadata XML's pin.
        bounds: West, south, east, north in metres of `epsg`.
        resolution_m: Pixel size in metres.
        epsg: The EPSG code the file must carry.
        tnm_project: The TNM project name, for the manifest.
    """

    snapshot_id: str
    tile: PinnedObject
    metadata_xml: PinnedObject
    bounds: tuple[float, float, float, float]
    resolution_m: float
    epsg: int
    tnm_project: str


X72Y429 = DemSource(
    snapshot_id="3dep-1m-mo-saintlouis-2017-x72y429",
    tile=PinnedObject(
        url=f"{_TNM}TIFF/USGS_one_meter_x72y429_MO_SaintLouis_2017.tif",
        bytes=483_356_883,
        etag='"7d4013fa90919a36b190a9e98ff8d13f-93"',
        last_modified="Fri, 13 Feb 2026 18:21:19 GMT",
    ),
    metadata_xml=PinnedObject(
        url=f"{_TNM}metadata/USGS_one_meter_x72y429_MO_SaintLouis_2017_meta.xml",
        bytes=12_154,
        etag='"6720211f551ef038c60fed8faf8bc9fc"',
        last_modified="Fri, 13 Feb 2026 18:33:44 GMT",
    ),
    bounds=(719_994.0, 4_279_994.0, 730_006.0, 4_290_006.0),
    resolution_m=1.0,
    epsg=26915,
    tnm_project="MO_SaintLouis_2017",
)


def check_dem_raster(path: Path, source: DemSource) -> dict[str, object]:
    """Check a DEM tile's header against its source and count its nodata pixels.

    Returns:
        Manifest fields: crs, grid, nodata_value and nodata_pixels.

    Raises:
        SnapshotError: for another CRS, pixel size, band count or extent.
    """
    with rasterio.open(path) as src:
        if src.crs is None or src.crs.to_epsg() != source.epsg:
            raise SnapshotError(f"{path.name}: CRS {src.crs} is not EPSG:{source.epsg}")
        if src.count != 1 or src.res != (source.resolution_m, source.resolution_m):
            raise SnapshotError(f"{path.name}: {src.count} bands at {src.res}, expected one band")
        if not np.allclose(tuple(src.bounds), source.bounds, rtol=0.0, atol=1e-6):
            raise SnapshotError(f"{path.name}: bounds {tuple(src.bounds)} not {source.bounds}")
        missing = 0
        for _, window in src.block_windows(1):
            block = src.read(1, window=window)
            gap = ~np.isfinite(block)
            if src.nodata is not None:
                gap |= block == src.nodata
            missing += int(gap.sum())
        return {
            "crs": f"EPSG:{source.epsg}",
            "grid": {
                "bounds": list(source.bounds),
                "resolution_m": source.resolution_m,
                "width": src.width,
                "height": src.height,
            },
            "nodata_value": src.nodata,
            "nodata_pixels": missing,
        }


def publication_date(xml: bytes) -> str | None:
    """The first `<pubdate>` of a TNM metadata file as an ISO date, or None."""
    found = _PUBDATE.search(xml)
    return None if found is None else "-".join(part.decode() for part in found.groups())


def build_dem(
    root: Path,
    *,
    source: DemSource = X72Y429,
    opener: Opener = urlopen_opener,
    today: date | None = None,
) -> Path:
    """Download, check and register-ready one 1 m DEM tile as a finished snapshot.

    Args:
        root: The snapshots folder.
        source: The tile and its pins; x72y429 by default.
        opener: urllib's opener, or a fake in tests.
        today: The capture date; the UTC date by default.

    Returns:
        The finished snapshot folder.

    Raises:
        PinMismatchError: when the remote file changed since it was pinned.
        SnapshotError: for an existing snapshot or a tile that fails its checks.
    """
    with timed("dem_snapshot", snapshot_id=source.snapshot_id) as details:

        def fill(partial: Path) -> dict[str, object]:
            records = fetch_all((source.tile, source.metadata_xml), partial, opener)
            details["input_sha256"] = records[0]["sha256"]
            raster = check_dem_raster(partial / str(records[0]["path"]), source)
            xml = (partial / str(records[1]["path"])).read_bytes()
            _write_dem_readme(partial, source)
            return {
                **raster,
                "source_objects": records,
                "horizontal_datum": HORIZONTAL_DATUM,
                "vertical_datum": VERTICAL_DATUM,
                "tnm_project": source.tnm_project,
                "tnm_publication_date": publication_date(xml),
            }

        metadata = {
            "capture_date": (today or today_utc()).isoformat(),
            "source_url": source.tile.url,
            "licence": LICENCE,
            "seeds": {},
        }
        folder = build_snapshot(root, source.snapshot_id, fill, metadata)
        details["path"] = str(folder)
    return folder


def _write_dem_readme(partial: Path, source: DemSource) -> None:
    write_readme(
        partial,
        source.snapshot_id,
        f"""
USGS 3DEP one-metre bare-earth DEM tile from TNM project {source.tnm_project}, with its
TNM metadata XML, downloaded unchanged. EPSG:{source.epsg} as labelled; the source lidar is
{HORIZONTAL_DATUM} / UTM 15N with heights in {VERTICAL_DATUM}. Public domain.

Written by `python -m pipeline.terrain_snapshots dem`. Never edited after the manifest.
""",
    )
