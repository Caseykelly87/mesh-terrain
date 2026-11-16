"""The snapshots a run names: found under SNAPSHOTS_DIR and verified before use (snp_01).

Each id in the config's `snapshots` is a finished folder with its manifest
(`docs/INTERFACE.md`); the engine needs no database row. Every file of every
named snapshot is checked against its manifest's sha256 with
`pipeline.common.snapshot` before the run starts, and the manifest's own
sha256 goes into the run manifest. The engine reads only files the manifest
lists: rasters (`.tif`) for the DEM, DSM and canopy roles, `buildings.parquet`
for the buildings role, and the map folder for P.1812.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from engine.config import SnapshotIds
from engine.errors import ConfigError
from pipeline.common.snapshot import MANIFEST_NAME, SnapshotError, verified_manifest

RASTER_SUFFIXES = (".tif", ".tiff")
BUILDINGS_FILE = "buildings.parquet"


@dataclass(frozen=True, kw_only=True)
class NamedSnapshot:
    """One verified snapshot folder.

    Attributes:
        role: The config role that named it.
        snapshot_id: Its id, the folder name.
        folder: The finished folder.
        manifest_sha256: sha256 of its `manifest.json` bytes.
        files: Every file its manifest lists, in manifest order.
    """

    role: str
    snapshot_id: str
    folder: Path
    manifest_sha256: str
    files: tuple[Path, ...]

    def rasters(self) -> list[Path]:
        """The listed GeoTIFFs, sorted by path, so overlapping tiles mosaic in a fixed order."""
        return sorted(f for f in self.files if f.suffix.lower() in RASTER_SUFFIXES)


def resolve_snapshots(ids: SnapshotIds, root: Path) -> dict[str, NamedSnapshot]:
    """Find and verify every named snapshot.

    Args:
        ids: Snapshot ids by role, from the config.
        root: The snapshots folder, `SNAPSHOTS_DIR`.

    Returns:
        Role to verified snapshot, for the roles the config names.

    Raises:
        ConfigError: naming the snapshot when its folder or manifest is
            missing, a file differs from the manifest, the manifest names
            another id, or the role's file is not in it.
    """
    snapshots = {
        role: _verified(role, snapshot_id, root) for role, snapshot_id in ids.named().items()
    }
    for role in ("dem", "dsm", "canopy"):
        if role in snapshots and not snapshots[role].rasters():
            raise ConfigError(f"snapshot {snapshots[role].snapshot_id} lists no GeoTIFF")
    buildings = snapshots.get("buildings")
    if buildings is not None and buildings.folder / BUILDINGS_FILE not in buildings.files:
        raise ConfigError(f"snapshot {buildings.snapshot_id} lists no {BUILDINGS_FILE}")
    return snapshots


def _verified(role: str, snapshot_id: str, root: Path) -> NamedSnapshot:
    folder = root / snapshot_id
    if not (folder / MANIFEST_NAME).is_file():
        raise ConfigError(f"snapshot {snapshot_id} ({role}): no finished folder at {folder}")
    try:
        manifest = verified_manifest(folder)
    except SnapshotError as err:
        raise ConfigError(f"snapshot {snapshot_id} ({role}): {err}") from err
    if manifest.get("snapshot_id") != snapshot_id:
        raise ConfigError(
            f"snapshot {snapshot_id}: its manifest names {manifest.get('snapshot_id')!r}"
        )
    manifest_bytes = (folder / MANIFEST_NAME).read_bytes()
    return NamedSnapshot(
        role=role,
        snapshot_id=snapshot_id,
        folder=folder,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        files=tuple(folder / entry["path"] for entry in manifest["files"]),
    )
