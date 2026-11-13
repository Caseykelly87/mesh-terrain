"""The ADR 0003 headline figures from analyze.py tallies, shared by the reference run and the gate.

`reference_from_archive.py` computes these figures from the real-id archive
and `reproduce_from_snapshot.py` from the pseudonymised snapshot; both call
`headline` with their own tally loader, so the only difference between the
two columns of the gate table is where the tallies come from. The figures are
the rows of analyze.py's "Headline" table (report_headline) plus the funnel of
the combined rule, computed with analyze.py's own functions. Aggregates only:
counts and rates, never an id, topic or coordinate.

analyze.py is imported unchanged. On Windows the C long is 32 bits, so its
`csv.field_size_limit(sys.maxsize)` overflows; the limit is clamped before the
import.
"""

from __future__ import annotations

import contextlib
import csv
import io
import math
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

SPIKE_DIR = Path(__file__).resolve().parent
REPO = SPIKE_DIR.parents[2]
MSA_CSV = REPO / "dbt" / "seeds" / "msa_boundary.csv"

_field_size_limit = csv.field_size_limit
csv.field_size_limit = lambda limit=None: (  # type: ignore[assignment]
    _field_size_limit() if limit is None else _field_size_limit(min(limit, 2**31 - 1))
)

if str(SPIKE_DIR) not in sys.path:
    sys.path.insert(0, str(SPIKE_DIR))

import analyze as az  # noqa: E402

# The shared 24 hours of results.md, as the spike's --start and --end gave them.
SHARED = ("2026-10-23T22:08:59+00:00", "2026-10-24T22:08:59+00:00")
# The seven spike runs behind results.md (README.md): broker, start, end, roots, borrowed broker.
RUNS: dict[str, tuple[str, str | None, str | None, str | None, str | None]] = {
    "public_full": ("public", None, None, None, None),
    "public_roots_mo_il_meshstl": ("public", None, None, "MO,IL,MeshSTL", None),
    "public_roots_mo_meshstl": ("public", None, None, "MO,MeshSTL", None),
    "public_shared_24h": ("public", *SHARED, None, None),
    "meshstl_24h": ("meshstl", *SHARED, None, None),
    "public_with_meshstl_positions": ("public", *SHARED, None, "meshstl"),
    "meshstl_with_public_positions": ("meshstl", *SHARED, None, "public"),
}
FUNNEL_STEPS = (
    "1 radio receipts with relay_node at area gateways",
    "3 lnk_01 relayer resolved",
    "5 lnk_02 both usable within the window",
    "6 lnk_05 500 m to 60 km",
)
# analyze.py's label for the last headline row, and the gate's.
POSITIONS_IGNORED = ("lnk_02 and lnk_05 ignored (diagnostic)", "positions ignored")

# (broker, start_s, end_s, roots) -> Tally
Loader = Callable[[str, float, float, frozenset[str] | None], Any]


WHOLE = (-math.inf, math.inf)


def bound(value: str | None, default: float) -> float:
    """Seconds since the epoch of an ISO 8601 bound, or the default when there is none."""
    return datetime.fromisoformat(value).timestamp() if value else default


def window_s(window: tuple[str, str]) -> tuple[float, float]:
    """[start, end) of an ISO 8601 window in seconds since the epoch."""
    return bound(window[0], -math.inf), bound(window[1], math.inf)


def run_tallies(load: Loader, name: str) -> Any:
    """The run's tally, with the other broker's positions added when the run borrows them."""
    broker, start, end, roots, borrow = RUNS[name]
    start_s, end_s = bound(start, -math.inf), bound(end, math.inf)
    tally = load(broker, start_s, end_s, frozenset(roots.split(",")) if roots else None)
    if borrow:
        with contextlib.redirect_stdout(io.StringIO()):
            az.borrow_positions(tally, load(borrow, start_s, end_s, None))
    return tally


def headline(load: Loader, name: str) -> dict[str, str]:
    """Link-hours a week with observed (relayers, gateways, pairs) per position rule, MSA.

    Args:
        load: Returns a fresh tally for (broker, start_s, end_s, roots).
        name: One of RUNS.

    Returns:
        Figure label to "week (observed; relayers, gateways, pairs)", plus the
        observed hours and the combined rule's funnel.
    """
    tally = run_tallies(load, name)
    msa = az.Area("the MSA (CBSA 41180)", az.load_area(MSA_CSV, {"41180"}))
    positions = az.area_positions(tally, msa)
    thirty = az.apply_rules(tally, positions)["combined"]
    stationary = az.apply_rules(tally, positions, stationary=True)["combined"]
    coverage = tally.coverage()
    measures = {
        "keep 30 minutes, 500 m": thirty.link_hours(az.MIN_DISTANCE_M),
        "keep 30 minutes, 3 km": thirty.link_hours(az.VALIDATION_DISTANCE_M),
        "stationarity window, 500 m": stationary.link_hours(az.MIN_DISTANCE_M),
        "stationarity window, 3 km": stationary.link_hours(az.VALIDATION_DISTANCE_M),
        "gateway-only fallback": thirty.gateway_only_hours(),
        POSITIONS_IGNORED[1]: az.position_free_groups(tally, positions),
    }
    figures: dict[str, str] = {"observed hours": f"{coverage.hours():.3f}"}
    for label, keys in measures.items():
        pairs = {(key[0], key[1]) for key in keys}
        week = coverage.rates(key[2] for key in keys).week_weighted
        relayers, gateways = len({p[0] for p in pairs}), len({p[1] for p in pairs})
        figures[label] = f"{week:.0f} ({len(keys)}; {relayers}, {gateways}, {len(pairs)})"
    figures["funnel"] = " / ".join(str(thirty.funnel[step]) for step in FUNNEL_STEPS)
    return figures


def printed_headline(text: str) -> dict[str, tuple[str, str]]:
    """(observed, week weighted) per row of the MSA headline table in analyze.py's output."""
    rows: dict[str, tuple[str, str]] = {}
    lines = iter(text.splitlines())
    for line in lines:
        if line.startswith("## Headline: link-hour rates inside the MSA"):
            break
    for line in lines:
        if line.startswith("## "):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 7 and cells[1].isdigit():
            label = POSITIONS_IGNORED[1] if cells[0] == POSITIONS_IGNORED[0] else cells[0]
            rows[label] = (cells[1], cells[6])
    return rows


def matches_printed(figures: dict[str, str], printed: dict[str, tuple[str, str]]) -> list[str]:
    """Labels whose week and observed count differ from analyze.py's printed table."""
    differing = []
    for label, (observed, week) in printed.items():
        value = figures.get(label, "")
        if not value.startswith(f"{week} ({observed};"):
            differing.append(label)
    return differing if printed else ["no headline table printed"]
