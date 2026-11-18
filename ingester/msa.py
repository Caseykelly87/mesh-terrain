"""The St. Louis MSA polygon (CBSA 41180) as a point test on stored integer coordinates.

The polygon is the dbt seed `dbt/seeds/msa_boundary.csv`, whose rows for one
geoid are WKT chunks joined in `part` order, the same polygon `pos_02` tests
in the warehouse and the conformance fixture's README names. The test reads a
position's stored `latitude_i` and `longitude_i` (1e-7 degrees, EPSG:4326),
including a 32-bit position's raw value rather than its cell centre (Casey,
PR #66, Q3). A point on the boundary is outside, as shapely's `contains`
gives.
"""

import csv
from collections.abc import Callable
from pathlib import Path
from typing import Any

import shapely

REPO_ROOT = Path(__file__).resolve().parents[1]
MSA_SEED = REPO_ROOT / "dbt" / "seeds" / "msa_boundary.csv"
MSA_GEOID = "41180"

InArea = Callable[[int, int], bool]
"""Whether latitude_i and longitude_i (1e-7 degrees, EPSG:4326) lie inside an area."""


def msa_polygon(seed: Path = MSA_SEED, geoid: str = MSA_GEOID) -> Any:
    """The MSA polygon from the seed, in EPSG:4326 degrees.

    Args:
        seed: The boundary CSV, with geoid, part and geometry_wkt columns.
        geoid: The CBSA code to read.

    Raises:
        ValueError: if the seed has no row for the geoid.
    """
    chunks: list[tuple[int, str]] = []
    with seed.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["geoid"] == geoid:
                chunks.append((int(row["part"] or 1), row["geometry_wkt"]))
    if not chunks:
        raise ValueError(f"no row for geoid {geoid} in {seed}")
    return shapely.from_wkt("".join(text for _, text in sorted(chunks)))


def msa_contains(seed: Path = MSA_SEED) -> InArea:
    """A cached point test for the MSA on 1e-7 degree integers.

    Args:
        seed: The boundary CSV.

    Returns:
        A function of latitude_i and longitude_i that is True strictly inside
        the polygon.
    """
    polygon = msa_polygon(seed)
    shapely.prepare(polygon)
    seen: dict[tuple[int, int], bool] = {}

    def contains(latitude_i: int, longitude_i: int) -> bool:
        key = (latitude_i, longitude_i)
        if key not in seen:
            seen[key] = bool(shapely.contains_xy(polygon, longitude_i / 1e7, latitude_i / 1e7))
        return seen[key]

    return contains
