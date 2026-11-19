"""Which point layers may reach the engine: synthetic ones, and real ones engine.prepare wrote.

engine.prepare checks the real layers it reads (every real row has its box,
ADR 0008). So that a layer reaches the engine only through those checks
(Casey, issue #55 comment 7018724580), prepare writes `prepared.json` beside
the layers it writes, naming each with its sha256, and the engine admits a
point layer only when its positions marker (`engine.positions_marker`) is
`synthetic`, or `real` with that file listing the layer's name and current
sha256. A real layer copied away from its `prepared.json`, or changed after
prepare wrote it, is refused, and so is a layer without the marker. The file
holds names and hashes only, no coordinate.

This stops accidents, not deliberate bypass (Casey, issue #55 comment
7024307502, item 2, Q1 (a)). It does not prove who wrote a `prepared.json`:
a hand-written one that lists a layer's name and sha256 is admitted, and so
is a real layer re-marked `synthetic`, since the marker is the caller's own
statement.
"""

import json
from collections.abc import Sequence
from pathlib import Path

from engine.errors import ConfigError
from engine.positions_marker import POSITIONS_KEY, REAL, SYNTHETIC, positions_marker
from pipeline.common.snapshot import sha256_file

STAMP_FILE = "prepared.json"
WRITTEN_BY = "engine.prepare"


def write_prepared_stamp(folder: Path, layer_names: Sequence[str], tile: str) -> Path:
    """Write `prepared.json` naming each layer engine.prepare wrote in a folder, with its sha256.

    Args:
        folder: The folder holding the layers, prepare's staging folder.
        layer_names: File names of the layers, as written in `folder`.
        tile: The tile the run was prepared for.

    Returns:
        The stamp's path.
    """
    stamp = {
        "written_by": WRITTEN_BY,
        "tile": tile,
        "layers": {name: sha256_file(folder / name) for name in layer_names},
    }
    path = folder / STAMP_FILE
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(stamp, indent=2, sort_keys=True) + "\n")
    return path


def require_admissible_layer(path: Path) -> None:
    """Refuse a point layer that is neither synthetic nor a real layer engine.prepare wrote.

    Args:
        path: A point layer the run config names, already read as GeoParquet.

    Raises:
        ConfigError: for a layer without the positions marker, with a marker
            other than `real` or `synthetic`, or marked `real` without a
            `prepared.json` beside it listing its name and current sha256.
    """
    marker = positions_marker(path)
    if marker == SYNTHETIC:
        return
    if marker is None:
        raise ConfigError(
            f"{path.name} has no {POSITIONS_KEY} marker: mark a made-up layer {SYNTHETIC}, "
            f"and write a real one with {WRITTEN_BY}"
        )
    if marker != REAL:
        raise ConfigError(f"{path.name}: {POSITIONS_KEY} is {marker!r}, not {REAL} or {SYNTHETIC}")
    listed = _stamped_layers(path.parent / STAMP_FILE, path.name)
    if path.name not in listed:
        raise ConfigError(
            f"{path.name} holds real positions and {STAMP_FILE} beside it does not list it; "
            f"only layers {WRITTEN_BY} wrote may hold real positions"
        )
    if listed[path.name] != sha256_file(path):
        raise ConfigError(
            f"{path.name}: its sha256 differs from the one {WRITTEN_BY} recorded in "
            f"{STAMP_FILE}, so the layer changed after prepare wrote it"
        )


def _stamped_layers(stamp: Path, layer_name: str) -> dict[str, str]:
    """The layers a `prepared.json` lists, by name; refuse a missing or unreadable one."""
    if not stamp.is_file():
        raise ConfigError(
            f"{layer_name} holds real positions and has no {STAMP_FILE} beside it; "
            f"only layers {WRITTEN_BY} wrote may hold real positions"
        )
    try:
        written = json.loads(stamp.read_text(encoding="utf-8"))
        layers = written["layers"] if written.get("written_by") == WRITTEN_BY else None
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as err:
        raise ConfigError(f"{stamp.name} beside {layer_name} is not readable: {err}") from err
    if not isinstance(layers, dict):
        raise ConfigError(f"{stamp.name} beside {layer_name} was not written by {WRITTEN_BY}")
    return {str(name): str(digest) for name, digest in layers.items()}
