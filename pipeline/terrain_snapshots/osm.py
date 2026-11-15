"""The OpenStreetMap snapshot: Geofabrik's Missouri and Illinois extracts of one date.

ADR 0006 item 9: the building outlines decide which endpoint draws land on a
building pixel, the building share near each endpoint (ter_05) and the Okumura
Hata variant (ter_06); the water outlines tell the DSM command which part of a
window is water, so its thin-output guard counts only land (Casey, unit 5b).
Both extracts carry the same first-of-month date, which is the snapshot's
capture_date and the end of its id, `osm-geofabrik-mo-il-YYYY-MM-DD`. Each is
checked against the md5 file Geofabrik publishes beside it, and kept.

From the two extracts the command reads, with pyrosm and a bounding box, the
building polygons and the water polygons, and writes each as GeoParquet in
EPSG:4326, sorted by OSM type and id with repeats across the state border
dropped. Only the outline and the OSM building tag are kept; heights come
from the DSM, never from OSM tags. The default box is the St. Louis MSA's,
rounded outwards to 0.01 degree, so one snapshot serves the test tile and the
corridors. Licence ODbL 1.0; any public output carries OSM_ATTRIBUTION.
"""

import re
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

import geopandas as gpd
import pyrosm

from pipeline.common.snapshot import SnapshotError
from pipeline.terrain_snapshots.common import build_snapshot, timed, today_utc, write_readme
from pipeline.terrain_snapshots.remote import Opener, download, read_small, urlopen_opener

BBox = tuple[float, float, float, float]
"""West, south, east, north in degrees, EPSG:4326."""

GEOFABRIK_US = "https://download.geofabrik.de/north-america/us/"
REGIONS = ("missouri", "illinois")
# Census TIGER 2025 CBSA 41180 is -91.4186, 38.0035, -89.1383, 39.5233 (dbt seed msa_boundary).
MSA_BBOX: BBox = (-91.42, 38.00, -89.13, 39.53)
LICENCE = "Open Database License (ODbL) 1.0"
OSM_ATTRIBUTION = "© OpenStreetMap contributors"
WATER_FILTER = {"natural": ["water"], "waterway": ["riverbank"], "landuse": ["reservoir", "basin"]}
_POLYGONAL = ("Polygon", "MultiPolygon")
_MD5_LINE = re.compile(r"([0-9a-f]{32})\s+\*?(\S+)")

Reader = Callable[[Sequence[Path], BBox], tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]]


def osm_snapshot_id(extract_date: date) -> str:
    """The snapshot id for an extract date, as ADR 0006 item 9 names it."""
    return f"osm-geofabrik-mo-il-{extract_date.isoformat()}"


def extract_name(region: str, extract_date: date) -> str:
    """Geofabrik's dated file name, for example `missouri-261101.osm.pbf`."""
    return f"{region}-{extract_date:%y%m%d}.osm.pbf"


def parse_md5_file(text: str, expected_name: str) -> str:
    """The md5 a Geofabrik `.md5` file gives for expected_name.

    Raises:
        SnapshotError: if the file names another extract or holds no md5 line.
    """
    found = _MD5_LINE.search(text)
    if found is None or found.group(2) != expected_name:
        raise SnapshotError(f"the md5 file for {expected_name} does not name it: {text[:80]!r}")
    return found.group(1)


def _columns(
    frame: gpd.GeoDataFrame, tag_column: str, tags: list[object]
) -> dict[str, list[object]]:
    """The polygon rows of one pyrosm frame as plain columns: id, osm_type, tag, geometry."""
    keep = frame.geom_type.isin(_POLYGONAL).tolist()
    picked = {
        "id": frame["id"].tolist(),
        "osm_type": frame["osm_type"].tolist(),
        tag_column: tags,
        "geometry": frame.geometry.tolist(),
    }
    return {
        name: [v for v, k in zip(values, keep, strict=True) if k] for name, values in picked.items()
    }


def _water_kind(frame: gpd.GeoDataFrame) -> list[object]:
    """The first WATER_FILTER tag each row carries, as `key=value`."""
    kinds: list[object] = [None] * len(frame)
    for key, values in WATER_FILTER.items():
        if key not in frame.columns:
            continue
        for row, value in enumerate(frame[key].tolist()):
            if kinds[row] is None and value in values:
                kinds[row] = f"{key}={value}"
    return kinds


def _layer(parts: list[dict[str, list[object]]], tag_column: str) -> gpd.GeoDataFrame:
    """One EPSG:4326 layer from the parts, repeats across extracts dropped, sorted."""
    names = ("id", "osm_type", tag_column, "geometry")
    merged = {name: [value for part in parts for value in part[name]] for name in names}
    geometry = merged.pop("geometry")
    frame = gpd.GeoDataFrame(merged, geometry=geometry, crs="EPSG:4326")
    frame = frame.drop_duplicates(subset=["osm_type", "id"])
    return frame.sort_values(["osm_type", "id"]).reset_index(drop=True)


def read_layers(pbf_paths: Sequence[Path], bbox: BBox) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Building and water polygons from OSM extracts inside a bounding box.

    Args:
        pbf_paths: The `.osm.pbf` extracts, read one at a time.
        bbox: West, south, east, north in degrees. pyrosm keeps features that
            touch it, and with complete relations each multipolygon, such as
            the Mississippi's, comes back whole rather than cut at the box.

    Returns:
        Buildings (id, osm_type, building, geometry) and water (id, osm_type,
        kind such as `natural=water`, geometry), EPSG:4326.
    """
    buildings: list[dict[str, list[object]]] = []
    water: list[dict[str, list[object]]] = []
    for path in pbf_paths:
        osm = pyrosm.OSM(str(path), bounding_box=list(bbox), complete_relations=True)
        found = osm.get_buildings()
        if found is not None:
            buildings.append(_columns(found, "building", found["building"].tolist()))
        wet = osm.get_data_by_custom_criteria(
            custom_filter=WATER_FILTER, filter_type="keep", keep_nodes=False
        )
        if wet is not None:
            water.append(_columns(wet, "kind", _water_kind(wet)))
    return _layer(buildings, "building"), _layer(water, "kind")


def _fetch_extract(url: str, dest: Path, opener: Opener) -> dict[str, object]:
    expected = parse_md5_file(
        read_small(f"{url}.md5", opener).decode("ascii", "replace"), dest.name
    )
    got = download(url, dest, opener)
    if got.md5 != expected:
        raise SnapshotError(f"{dest.name}: md5 {got.md5} is not Geofabrik's {expected}")
    return {"url": url, "path": dest.name, "bytes": got.bytes, "md5": got.md5, "sha256": got.sha256}


def build_osm(
    root: Path,
    extract_date: date,
    *,
    opener: Opener = urlopen_opener,
    reader: Reader = read_layers,
    bbox: BBox = MSA_BBOX,
    regions: Sequence[str] = REGIONS,
    base_url: str = GEOFABRIK_US,
) -> Path:
    """Download the dated extracts, check their md5, and write the building and water layers.

    Args:
        root: The snapshots folder.
        extract_date: Geofabrik's extract date; the first of a month (ADR 0006 item 9).
        opener: urllib's opener, or a fake in tests.
        reader: Reads the two layers from the extracts; read_layers in production.
        bbox: The box the layers are read inside, degrees, EPSG:4326.
        regions: Geofabrik region names, Missouri and Illinois by default.
        base_url: The Geofabrik folder holding the regions.

    Returns:
        The finished snapshot folder.

    Raises:
        SnapshotError: for a date that is not the first of a month, an existing
            snapshot, or an extract whose md5 differs from Geofabrik's.
    """
    if extract_date.day != 1:
        raise SnapshotError(f"{extract_date} is not the first of a month; Geofabrik keeps those")
    snapshot_id = osm_snapshot_id(extract_date)
    urls = [f"{base_url}{extract_name(region, extract_date)}" for region in regions]
    with timed("osm_snapshot", snapshot_id=snapshot_id) as details:

        def fill(partial: Path) -> dict[str, object]:
            records = [_fetch_extract(url, partial / url.rsplit("/", 1)[1], opener) for url in urls]
            details["input_sha256"] = [record["sha256"] for record in records]
            buildings, water = reader([partial / str(r["path"]) for r in records], bbox)
            buildings.to_parquet(partial / "buildings.parquet", index=False)
            water.to_parquet(partial / "water.parquet", index=False)
            details.update(buildings=len(buildings), water=len(water))
            _write_osm_readme(partial, extract_date)
            return {
                "source_objects": records,
                "attribution": OSM_ATTRIBUTION,
                "bbox_epsg4326": list(bbox),
                "layers": {"buildings.parquet": len(buildings), "water.parquet": len(water)},
                "water_filter": WATER_FILTER,
                "pyrosm_version": pyrosm.__version__,
                "downloaded_on": today_utc().isoformat(),
            }

        metadata = {
            "capture_date": extract_date.isoformat(),
            "source_url": urls,
            "licence": LICENCE,
            "seeds": {},
        }
        folder = build_snapshot(root, snapshot_id, fill, metadata)
        details["path"] = str(folder)
    return folder


def _write_osm_readme(partial: Path, extract_date: date) -> None:
    write_readme(
        partial,
        osm_snapshot_id(extract_date),
        f"""
Geofabrik's OpenStreetMap extracts for {", ".join(REGIONS)} dated {extract_date.isoformat()},
each checked against Geofabrik's md5 file, and two layers read from them with pyrosm inside
the bounding box in the manifest: `buildings.parquet` (building outlines) and `water.parquet`
(natural=water, waterway=riverbank, landuse=reservoir or basin), GeoParquet in EPSG:4326.

Licence: {LICENCE}. Any public output that uses this snapshot carries "{OSM_ATTRIBUTION}".

Written by `python -m pipeline.terrain_snapshots osm`. Never edited after the manifest.
""",
    )
