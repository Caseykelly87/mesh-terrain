"""Synthetic DEM and DSM snapshots for the tests of `engine.prepare`.

Each is a finished snapshot folder with its manifest, written through
`pipeline.common.snapshot` as a real one is, under a test's own folder. Every
height in them is made up.
"""

from pathlib import Path

import numpy as np
import numpy.typing as npt
from terrain_dem import write_dem

from engine.config import SnapshotIds
from engine.snapshots import NamedSnapshot, resolve_snapshots
from pipeline.common.snapshot import finish_snapshot, start_snapshot, write_manifest

Raster = tuple[npt.NDArray[np.float32], float, tuple[float, float]]
"""Heights in metres, pixel size in metres, top-left corner in EPSG:26915."""


def write_raster_snapshot(root: Path, snapshot_id: str, rasters: dict[str, Raster]) -> Path:
    """Write a finished snapshot holding the given GeoTIFFs."""
    partial = start_snapshot(root, snapshot_id)
    for name, (heights_m, resolution_m, origin_xy_m) in rasters.items():
        write_dem(partial / name, heights_m, resolution_m=resolution_m, origin_xy_m=origin_xy_m)
    (partial / "README.md").write_text("Synthetic test raster.\n", encoding="utf-8")
    metadata = {
        "snapshot_id": snapshot_id,
        "capture_date": "2026-11-16",
        "source_url": "none: made-up values written by a test",
        "licence": "MIT, as the repository; no third-party data",
        "seeds": {},
    }
    write_manifest(partial, metadata)
    return finish_snapshot(partial)


def named(root: Path, dem_id: str, dsm_id: str | None = None) -> dict[str, NamedSnapshot]:
    """The verified snapshots by role, as the engine resolves them."""
    ids = SnapshotIds(dem=dem_id, dsm=dsm_id, canopy=None, buildings=None, p1812_maps=None)
    return resolve_snapshots(ids, root)
