"""`manifest.json`: the run's inputs and artifacts with their checksums, written last.

The fields of `docs/INTERFACE.md` (manifest_version 1.0.0) plus the six code
provenance fields of `engine.provenance` (Casey, PR #32), which make it
1.1.0. Every input and artifact carries its file sha256; every artifact also
carries its `content_sha256` (`engine.hashing`), and Parquet artifacts their
row count. Input paths are written as the config names them, and profile
files as `profiles/<name>.yaml`. The manifest is the only file of a run that
holds a time, so it is never content-hashed.
"""

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from engine import ENGINE_VERSION, MANIFEST_VERSION, SCHEMA_VERSION
from engine.config import RunConfig
from engine.hashing import content_sha256
from engine.provenance import Provenance
from engine.snapshots import NamedSnapshot
from pipeline.common.snapshot import sha256_file
from terrain.path_loss import Model

MANIFEST_NAME = "manifest.json"


def model_versions(models: tuple[Model, ...], provenance: Provenance) -> dict[str, str]:
    """What implements each model of the run, for the manifest's `models`."""
    names = {
        "fspl": "free space, ITU-R P.525-5 equation (5)",
        "knife_edge": "free space plus Bullington, ITU-R P.526-16 section 4.5.1",
        "hata": "Okumura-Hata (Hata 1980), small and medium city a(hm)",
        "itm": f"NTIA ITM {(provenance.itm_source_commit or '')[:8]}",
        "p1812": f"Py1812 {(provenance.py1812_commit or '')[:8]}, P.1812-8",
    }
    return {model: names[model] for model in models}


def artifact_entry(folder: Path, path: Path) -> dict[str, Any]:
    """One artifact's manifest entry, its path relative to the run folder."""
    entry: dict[str, Any] = {
        "path": path.relative_to(folder).as_posix(),
        "sha256": sha256_file(path),
        "content_sha256": content_sha256(path),
    }
    if path.suffix == ".parquet":
        entry["rows"] = pq.read_metadata(path).num_rows
    return entry


def input_entries(config: RunConfig) -> list[dict[str, str]]:
    """The run's input files: point layers as the config names them, then profiles."""
    paths = {"tx_sites": config.tx_sites, "rx_points": config.rx_points}
    if config.calibration is not None:
        paths["calibration"] = config.calibration
    entries = [
        {"path": config.input_names[field], "sha256": sha256_file(path)}
        for field, path in paths.items()
    ]
    entries += [
        {"path": f"profiles/{path.name}", "sha256": sha256_file(path)}
        for path in dict.fromkeys(config.profile_files)
    ]
    return entries


def write_run_manifest(
    folder: Path,
    config: RunConfig,
    snapshots: dict[str, NamedSnapshot],
    provenance: Provenance,
    artifacts: list[Path],
) -> Path:
    """Write `manifest.json` into the run's `.partial` folder, after every artifact.

    Args:
        folder: The run's `.partial` folder.
        config: The run's config.
        snapshots: The verified snapshots by role.
        provenance: The code provenance fields.
        artifacts: Every artifact written, in the order written.

    Returns:
        The manifest's path.
    """
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "run_id": config.run_id,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "config_sha256": config.config_sha256,
        "seed": config.seed,
        "models": model_versions(config.models, provenance),
        **asdict(provenance),
        "snapshots": [
            {"id": s.snapshot_id, "manifest_sha256": s.manifest_sha256} for s in snapshots.values()
        ],
        "inputs": input_entries(config),
        "artifacts": [artifact_entry(folder, path) for path in artifacts],
    }
    path = folder / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path
