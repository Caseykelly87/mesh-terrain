"""Expected staging and core values for every position in the synthetic fixtures.

The dbt tests compare `stg_position` and `core.fct_node_position` with the seed
`dbt/seeds/fixtures/expected_fixture_positions.csv`. This module computes that
seed in Python, apart from the SQL, with the arithmetic the link-rules README
names: the firmware grid in integers, `pos_01`'s WGS84 formula for the
half-widths, and shapely `contains` on the polygon joined from
`dbt/seeds/msa_boundary.csv` for the MSA. `tests/test_fixture_positions.py`
fails when the committed seed differs from what this computes, and checks the
computed cells against the boxes worked out by hand in the link-rules cases.

Regenerate the seed after a fixture change with
`uv run python tests/fixture_positions.py`.
"""

import csv
import io
import math
import sys
from collections.abc import Iterable
from functools import cache
from pathlib import Path
from typing import Any

from shapely import wkt
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixture_rows import REPO_ROOT, all_rows  # noqa: E402

SEED = REPO_ROOT / "dbt" / "seeds" / "fixtures" / "expected_fixture_positions.csv"
MSA_CSV = REPO_ROOT / "dbt" / "seeds" / "msa_boundary.csv"

# WGS84 (pos_01).
SEMI_MAJOR_M = 6378137.0
FLATTENING = 1 / 298.257223563
E2 = FLATTENING * (2 - FLATTENING)

COLUMNS = (
    "snapshot_id",
    "delivery_key",
    "node_pseudonym",
    "position_source",
    "position_zero_mapped",
    "latitude_i",
    "longitude_i",
    "precision_bits",
    "position_quality",
    "cell_width_i",
    "south_i",
    "north_i",
    "west_i",
    "east_i",
    "half_width_ns_m",
    "half_width_ew_m",
    "precise_source",
    "inside_msa",
    "in_core",
)


@cache
def msa_polygon() -> BaseGeometry:
    """The MSA boundary, its WKT chunks joined in part order."""
    csv.field_size_limit(1_000_000)
    with MSA_CSV.open(encoding="utf-8", newline="") as handle:
        chunks = sorted(csv.DictReader(handle), key=lambda row: int(row["part"]))
    return wkt.loads("".join(chunk["geometry_wkt"] for chunk in chunks))


def inside_msa(latitude_i: float, longitude_i: float) -> bool:
    """Whether a stored value, in 1e-7 degrees, lies inside the MSA (pos_02, Casey Q3)."""
    return bool(msa_polygon().contains(Point(longitude_i * 1e-7, latitude_i * 1e-7)))


def quality(bits: int | None) -> str:
    if bits is not None and 15 <= bits <= 32:
        return "usable"
    if bits is not None and 12 <= bits <= 14:
        return "coarse"
    return "unusable"


def cell_width(bits: int | None) -> int | None:
    """2^(32 - bits) in 1e-7 degrees, or None at 0 bits and outside 0 to 32 (pos_01)."""
    return 2 ** (32 - bits) if bits is not None and 1 <= bits <= 32 else None


def cell_low(value: int, bits: int, width: int) -> int:
    """The low edge: the stored centre minus half the width, or the value itself at 32 bits."""
    return value if bits == 32 else value - width // 2


def half_widths(width: int, latitude_i: int) -> tuple[float, float]:
    """North-south and east-west half-widths in metres at the stored latitude (pos_01)."""
    phi = math.radians(latitude_i * 1e-7)
    denominator = 1 - E2 * math.sin(phi) ** 2
    meridian = SEMI_MAJOR_M * (1 - E2) / denominator**1.5
    prime_vertical = SEMI_MAJOR_M / math.sqrt(denominator)
    angle = width * 0.5 * 1e-7 * math.pi / 180
    return angle * meridian, angle * prime_vertical * math.cos(phi)


def zero_mapped(row: dict[str, Any]) -> bool:
    """A Position at 0, 0, which staging maps to no position (pos_03)."""
    return bool(
        row["position_source"] == "position" and row["latitude_i"] == 0 and row["longitude_i"] == 0
    )


def expected_position(row: dict[str, Any]) -> dict[str, Any]:
    """What stg_position holds for one raw position row, and whether it is inside the MSA."""
    mapped = zero_mapped(row)
    has_position = not mapped and row["latitude_i"] is not None and row["longitude_i"] is not None
    bits = row["precision_bits"] if has_position else None
    width = cell_width(bits) if has_position else None
    expected: dict[str, Any] = {
        "snapshot_id": row["snapshot_id"],
        "delivery_key": row["delivery_key"],
        "node_pseudonym": row["origin_pseudonym"],
        "position_source": row["position_source"],
        "position_zero_mapped": mapped,
        "latitude_i": None if mapped else row["latitude_i"],
        "longitude_i": None if mapped else row["longitude_i"],
        "precision_bits": bits,
        "position_quality": quality(bits) if has_position else None,
        "cell_width_i": width,
        "precise_source": (bits >= 20 and row["seen_meshstl"]) if bits is not None else None,
        "inside_msa": has_position and inside_msa(row["latitude_i"], row["longitude_i"]),
    }
    expected |= cell_columns(row, bits, width)
    return expected


def cell_columns(row: dict[str, Any], bits: int | None, width: int | None) -> dict[str, Any]:
    if bits is None or width is None:
        return dict.fromkeys(("south_i", "north_i", "west_i", "east_i", "half_width_ns_m", "half_width_ew_m"))
    south = cell_low(row["latitude_i"], bits, width)
    west = cell_low(row["longitude_i"], bits, width)
    north_south, east_west = half_widths(width, row["latitude_i"])
    return {
        "south_i": south,
        "north_i": south + width,
        "west_i": west,
        "east_i": west + width,
        "half_width_ns_m": north_south,
        "half_width_ew_m": east_west,
    }


def mark_in_core(rows: list[dict[str, Any]], raw: list[dict[str, Any]]) -> None:
    """Core keeps one row per delivery inside the MSA: the first copy by received_at, then snapshot."""
    received = {(r["snapshot_id"], r["delivery_key"]): r["received_at"] for r in raw}
    first: dict[str, tuple[Any, str]] = {}
    for row in rows:
        if row["inside_msa"]:
            order = (received[(row["snapshot_id"], row["delivery_key"])], row["snapshot_id"])
            first[row["delivery_key"]] = min(first.get(row["delivery_key"], order), order)
    for row in rows:
        order = (received[(row["snapshot_id"], row["delivery_key"])], row["snapshot_id"])
        row["in_core"] = row["inside_msa"] and first[row["delivery_key"]] == order


def expected_positions() -> list[dict[str, Any]]:
    """One expected row per fixture position message, ordered by snapshot and delivery key."""
    raw = [row for row in all_rows() if row["position_source"] is not None]
    rows = [expected_position(row) for row in raw]
    mark_in_core(rows, raw)
    return sorted(rows, key=lambda row: (row["snapshot_id"], row["delivery_key"]))


def cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.9f}"
    return str(value)


def seed_text(rows: Iterable[dict[str, Any]]) -> str:
    """The seed CSV: a header, then one line per row, empty for null."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow([cell_text(row[column]) for column in COLUMNS])
    return buffer.getvalue()


if __name__ == "__main__":
    SEED.parent.mkdir(parents=True, exist_ok=True)
    SEED.write_text(seed_text(expected_positions()), encoding="utf-8", newline="\n")
    print(f"wrote {SEED.relative_to(REPO_ROOT).as_posix()}")
