"""The canopy height snapshot: one Meta and WRI CHMv2 tile and its imagery-date file.

The canopy height map gives tree heights in whole metres from satellite
imagery, mostly leaf-on, beside the leaf-off lidar DSM (ADR 0006 decision 6).
Casey chose (unit 5b (c) C1) tile 0231113110, the one zoom-10 tile that holds
all of x72y429 with about 218 m to spare at its east edge, plus the tile's
metadata GeoJSON, whose polygons carry the imagery date (`acq_date`) behind
each part of the map. Both are kept as downloaded.

The Meta bucket has no object versioning, so a tile can be replaced in place.
The command checks size, ETag and Last-Modified before downloading, then
records the sha256 beside them in the manifest (Casey, unit 5b). It also checks
that the tile is uint8 in EPSG:3857 and covers the target extent.

The snapshot keeps EPSG:3857. The engine build resamples the canopy onto the
DEM's 1 m EPSG:26915 grid by nearest neighbour (Casey, unit 5b (b) B1), which
keeps both the heights and the share of cells with canopy as the map has
them; masked pixels count as canopy 0 (ADR 0006 item 11 (i)).
"""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import rasterio
from rasterio.warp import transform_bounds

from pipeline.common.snapshot import SnapshotError
from pipeline.terrain_snapshots.common import build_snapshot, timed, today_utc, write_readme
from pipeline.terrain_snapshots.remote import Opener, PinnedObject, fetch_all, urlopen_opener

LICENCE = (
    "CC BY 4.0 (AWS Open Data registry entry); DINOv3 License (Brandt et al. 2026, "
    "arXiv 2603.06382); both recorded, attribution to Meta and WRI for both"
)
ATTRIBUTION = "Canopy height: Meta and World Resources Institute, CHMv2"
RESAMPLING = "nearest neighbour onto the DEM's 1 m EPSG:26915 grid, in the engine build (B1)"
_BASE = "https://dataforgood-fb-data.s3.amazonaws.com/forests/v2/global/dinov3_global_chm_v2_ml3/"


@dataclass(frozen=True)
class CanopySource:
    """A canopy tile, its date file, and the EPSG:26915 extent it must cover."""

    snapshot_id: str
    tile: PinnedObject
    dates: PinnedObject
    target_bounds_26915: tuple[float, float, float, float]


TILE_0231113110 = CanopySource(
    snapshot_id="canopy-v2-0231113110",
    tile=PinnedObject(
        url=f"{_BASE}chm/0231113110.tif",
        bytes=220_436_367,
        etag='"bcabd5820b097fbac3911603d2ba875f-27"',
        last_modified="Wed, 07 Jan 2026 08:47:55 GMT",
    ),
    dates=PinnedObject(
        url=f"{_BASE}metadata/0231113110.geojson",
        bytes=18_706_185,
        etag='"f6a60e0a8d17ad3a72a5296bff7559f9-3"',
        last_modified="Wed, 31 Dec 2025 12:07:31 GMT",
    ),
    target_bounds_26915=(719_994.0, 4_279_994.0, 730_006.0, 4_290_006.0),
)


def check_canopy_tile(path: Path, target: tuple[float, float, float, float]) -> dict[str, object]:
    """Check a canopy tile's header: uint8, EPSG:3857, covering the target.

    Args:
        path: The downloaded tile.
        target: West, south, east, north in EPSG:26915 metres.

    Raises:
        SnapshotError: for another type or CRS, or a tile that does not cover the target.
    """
    with rasterio.open(path) as src:
        if src.dtypes[0] != "uint8" or src.crs is None or src.crs.to_epsg() != 3857:
            raise SnapshotError(f"{path.name} is {src.dtypes[0]} in {src.crs}, not uint8 EPSG:3857")
        need = transform_bounds("EPSG:26915", "EPSG:3857", *target, densify_pts=21)
        have = tuple(src.bounds)
        if not (
            have[0] <= need[0] and have[1] <= need[1] and have[2] >= need[2] and have[3] >= need[3]
        ):
            raise SnapshotError(f"{path.name} {have} does not cover {need} (EPSG:3857)")
        return {
            "crs": "EPSG:3857",
            "tile_bounds_epsg3857": list(have),
            "pixel_size_m": list(src.res),
            "covers_epsg26915": list(target),
        }


def acq_date_range(path: Path) -> list[str]:
    """The earliest and latest `acq_date` in a CHMv2 metadata GeoJSON.

    Raises:
        SnapshotError: when no feature carries a date.
    """
    collection = json.loads(path.read_text(encoding="utf-8"))
    days = sorted(
        str(feature["properties"]["acq_date"])
        for feature in collection.get("features", [])
        if (feature.get("properties") or {}).get("acq_date")
    )
    if not days:
        raise SnapshotError(f"{path.name} holds no acq_date")
    return [days[0], days[-1]]


def build_canopy(
    root: Path,
    *,
    source: CanopySource = TILE_0231113110,
    opener: Opener = urlopen_opener,
    today: date | None = None,
) -> Path:
    """Download and check a canopy tile and its date file as a finished snapshot.

    Raises:
        PinMismatchError: before download when a header differs from the pin.
        SnapshotError: for an existing snapshot or a tile that fails its checks.
    """
    with timed("canopy_snapshot", snapshot_id=source.snapshot_id) as details:

        def fill(partial: Path) -> dict[str, object]:
            records = fetch_all((source.tile, source.dates), partial, opener)
            details["input_sha256"] = [record["sha256"] for record in records]
            header = check_canopy_tile(
                partial / str(records[0]["path"]), source.target_bounds_26915
            )
            _write_canopy_readme(partial, source)
            return {
                **header,
                "source_objects": records,
                "acq_date_range": acq_date_range(partial / str(records[1]["path"])),
                "attribution": ATTRIBUTION,
                "resampling": RESAMPLING,
                "units": "whole metres of canopy height; masked pixels have no value",
            }

        metadata = {
            "capture_date": (today or today_utc()).isoformat(),
            "source_url": [source.tile.url, source.dates.url],
            "licence": LICENCE,
            "seeds": {},
        }
        folder = build_snapshot(root, source.snapshot_id, fill, metadata)
        details["path"] = str(folder)
    return folder


def _write_canopy_readme(partial: Path, source: CanopySource) -> None:
    write_readme(
        partial,
        source.snapshot_id,
        f"""
Meta and WRI canopy height map v2 (CHMv2): tile `{source.tile.url.rsplit("/", 1)[1]}` and its
imagery-date GeoJSON, as downloaded, EPSG:3857, uint8 metres with an internal mask. The engine
resamples it {RESAMPLING}.

Licence: {LICENCE}. Credit: {ATTRIBUTION}.

Written by `python -m pipeline.terrain_snapshots canopy`. Never edited after the manifest.
""",
    )
