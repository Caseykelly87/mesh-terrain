"""The ITU-R P.1812 map snapshot: the ITU's zip and the two refractivity maps it holds.

P.1812 reads two climate maps at the path centre: DN50.TXT, how fast the air's
refractivity falls over the lowest kilometre (N-units/km), and N050.TXT, the
refractivity at sea level (N-units). terrain.p1812.load_maps reads both from
this snapshot and fails loudly without it (ADR 0006 decision 2). The command
downloads the zip, checks its size and sha256 against ADR 0006's spike, reads
only the two named members (never extracting the archive as a whole, so no
member can write outside the folder), checks each member's sha256, and keeps
the zip and the two files. It never builds P1812.npz.

The zip carries no licence of its own. The licence recorded is the sentence on
page ii of Recommendation ITU-R P.1812-8 (09/2025), the publication the maps
belong to. The snapshot is held privately: never committed, never published.
"""

import hashlib
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pipeline.common.snapshot import SnapshotError
from pipeline.terrain_snapshots.common import build_snapshot, timed, today_utc, write_readme
from pipeline.terrain_snapshots.remote import Opener, PinnedObject, fetch_pinned, urlopen_opener

ITU_LICENCE = (
    "© ITU 2025. All rights reserved. No part of this publication may be reproduced, "
    "by any means whatsoever, without written permission of ITU."
)
LICENCE_SOURCE = "Recommendation ITU-R P.1812-8 (09/2025), page ii; the zip holds no licence file"


@dataclass(frozen=True)
class ItuMapsSource:
    """The map zip, the name it is kept under, and the sha256 of each map it must hold."""

    snapshot_id: str
    archive: PinnedObject
    archive_name: str
    members: Mapping[str, str]


P1812_8_MAPS = ItuMapsSource(
    snapshot_id="itu-r-p1812-8-202509",
    archive=PinnedObject(
        url="https://www.itu.int/dms_pubrec/itu-r/rec/p/R-REC-P.1812-8-202509-I!!ZIP-E.zip",
        bytes=635_849,
        sha256="d3a51f296e24f5d4f99daddcac8a38cb310fcb2081efef5d120f8f27bd8c5686",
    ),
    archive_name="R-REC-P.1812-8-202509-I.zip",
    members={
        "DN50.TXT": "4847bf9542b775850daab6f0fb1bdc933a4d9a28aff65549bcfc45e141cd230d",
        "N050.TXT": "d27590ce5c7cf7ff7b4457ce31b9bf3591f0b97e367732195de0f1a98bd35f2a",
    },
)


def extract_members(archive_path: Path, members: Mapping[str, str], dest: Path) -> list[str]:
    """Write each named member of a zip into dest after checking its sha256.

    Returns:
        The member names written, in the order given.

    Raises:
        SnapshotError: for a member that is missing or whose sha256 differs.
    """
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        for name, expected in members.items():
            if name not in names:
                raise SnapshotError(f"{archive_path.name} holds no {name}")
            body = archive.read(name)
            if hashlib.sha256(body).hexdigest() != expected:
                raise SnapshotError(
                    f"{name} in {archive_path.name} does not match its pinned sha256"
                )
            (dest / name).write_bytes(body)
    return list(members)


def build_itu_maps(
    root: Path,
    *,
    source: ItuMapsSource = P1812_8_MAPS,
    opener: Opener = urlopen_opener,
    today: date | None = None,
) -> Path:
    """Download and check the ITU map zip, keep it and its two maps as a finished snapshot.

    Raises:
        PinMismatchError: when the zip's size or sha256 is not the pinned one.
        SnapshotError: for an existing snapshot or a missing or changed map.
    """
    with timed("itu_maps_snapshot", snapshot_id=source.snapshot_id) as details:

        def fill(partial: Path) -> dict[str, object]:
            record = fetch_pinned(source.archive, partial / source.archive_name, opener)
            details["input_sha256"] = record["sha256"]
            extracted = extract_members(partial / source.archive_name, source.members, partial)
            _write_itu_readme(partial, source)
            return {
                "source_objects": [record],
                "extracted": extracted,
                "licence_source": LICENCE_SOURCE,
                "redistribution": "never committed or published",
            }

        metadata = {
            "capture_date": (today or today_utc()).isoformat(),
            "source_url": source.archive.url,
            "licence": ITU_LICENCE,
            "seeds": {},
        }
        folder = build_snapshot(root, source.snapshot_id, fill, metadata)
        details["path"] = str(folder)
    return folder


def _write_itu_readme(partial: Path, source: ItuMapsSource) -> None:
    write_readme(
        partial,
        source.snapshot_id,
        f"""
The ITU's digital maps for ITU-R P.1812: the zip `{source.archive_name}` as downloaded from
{source.archive.url}, and the two maps P.1812 reads, `DN50.TXT` (median annual refractivity
lapse rate over the lowest 1 km, N-units/km) and `N050.TXT` (median sea-level surface
refractivity, N-units), each checked against its pinned sha256. No P1812.npz is built.

Licence: {ITU_LICENCE} ({LICENCE_SOURCE}.) Held privately; never committed or published.

Written by `python -m pipeline.terrain_snapshots itu-maps`. Never edited after the manifest.
""",
    )
