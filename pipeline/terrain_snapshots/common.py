"""What every terrain snapshot command shares: timed log lines and the snapshot lifecycle.

build_snapshot wraps pipeline.common.snapshot: it creates `<id>.partial/`,
lets the command fill it, writes the manifest and renames the folder. It
refuses an existing final or `.partial` folder before the command does any
work, so no command downloads anything for a snapshot it cannot write. A
command that fails leaves its `.partial` folder where it is; Casey decides
what happens to it (ADR 0005 item 6).
"""

import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import structlog

from pipeline.common import snapshot

log = structlog.get_logger(component="pipeline.terrain_snapshots")

Fill = Callable[[Path], Mapping[str, object]]
"""Writes a command's files into the `.partial` folder and returns its manifest fields."""


def today_utc() -> date:
    """The UTC date, recorded as a download snapshot's capture_date."""
    return datetime.now(UTC).date()


@contextmanager
def timed(command: str, **context: object) -> Iterator[dict[str, Any]]:
    """Log `<command>_begin`, then `<command>_end` with the duration and what the block recorded."""
    bound = log.bind(**context)
    bound.info(f"{command}_begin")
    started = time.monotonic()
    details: dict[str, Any] = {}
    yield details
    bound.info(f"{command}_end", duration_s=round(time.monotonic() - started, 3), **details)


def build_snapshot(
    root: Path, snapshot_id: str, fill: Fill, metadata: Mapping[str, object]
) -> Path:
    """Create, fill, manifest and finish one snapshot folder.

    Args:
        root: The snapshots folder (SNAPSHOTS_DIR in production).
        snapshot_id: The new snapshot's id.
        fill: Writes the files into the `.partial` folder; returns manifest fields.
        metadata: The snp_01 fields other than snapshot_id.

    Returns:
        The finished `<root>/<id>/` folder.

    Raises:
        SnapshotExistsError, PartialExistsError: before fill runs.
        SnapshotError: from fill or the manifest.
    """
    partial = snapshot.start_snapshot(root, snapshot_id)
    fields = fill(partial)
    snapshot.write_manifest(partial, {**metadata, **fields, "snapshot_id": snapshot_id})
    return snapshot.finish_snapshot(partial)


def write_readme(partial: Path, title: str, body: str) -> None:
    """Write the snapshot's README.md: a title line and a few plain paragraphs."""
    (partial / "README.md").write_text(f"# {title}\n\n{body.strip()}\n", encoding="utf-8")
