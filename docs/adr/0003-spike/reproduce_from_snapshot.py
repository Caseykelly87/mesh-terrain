"""Reproduction gate: every ADR 0003 link figure from the pseudonymised snapshot alone.

Reads only the snapshot folder (after checking every file against its
manifest): the delivery rows, the count tables, coverage.json and the
manifest. Each pseudonym becomes a stand-in integer whose low byte is the
stored low byte, so the committed analyze.py runs unchanged on the rows:
relay_node resolution matches low bytes exactly as it did on real ids. The
broker flags pick each broker's rows; coverage.json gives each broker's
observed minutes. The real-id archive is never opened.

Writes, under OUT_DIR, the full analyze.py output of the seven spike runs and
gate.md, the comparison of the results.md figures with the reproduced ones,
including merged_cross_broker and merged_repeat per broker. Aggregates only:
no pseudonym, stand-in id, topic or coordinate is printed.

Usage, from the repository root, Git Bash (shapely and pyproj are the spike
Dockerfile's pins and enter no lock file):

    uv run --frozen --with shapely==2.2.0 --with pyproj==3.8.0 python -I \
        docs/adr/0003-spike/reproduce_from_snapshot.py \
        C:/Users/Casey/mesh-terrain-data/snapshots/meshtastic-capture-2026-10-22 OUT_DIR
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import math
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

SPIKE_DIR = Path(__file__).resolve().parent
REPO = SPIKE_DIR.parents[2]
sys.path.insert(0, str(SPIKE_DIR))

# On Windows the C long is 32 bits, so analyze.py's csv.field_size_limit(sys.maxsize)
# overflows; clamp it before the import, as the lead's pairs wrapper does.
_field_size_limit = csv.field_size_limit
csv.field_size_limit = lambda limit=None: (  # type: ignore[assignment]
    _field_size_limit() if limit is None else _field_size_limit(min(limit, 2**31 - 1))
)

import analyze as az  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from ingester.snapshot import read_manifest, verify_manifest  # noqa: E402
from ingester.topics import topic_class, topic_root  # noqa: E402

MSA_CSV = REPO / "dbt" / "seeds" / "msa_boundary.csv"
SHARED = ("2026-10-23T22:08:59+00:00", "2026-10-24T22:08:59+00:00")
BROKER_FLAG = {"public": "seen_public", "meshstl": "seen_meshstl"}
COLUMNS = [
    "received_at", "topic", "decode_status", "gateway_pseudonym", "gateway_low_byte",
    "origin_pseudonym", "origin_low_byte", "packet_id", "relay_node", "hop_start", "hop_limit",
    "via_mqtt", "rx_snr", "rx_rssi", "portnum", "position_source", "latitude_i", "longitude_i",
    "precision_bits", "neighbour_pseudonyms", "neighbour_low_bytes", "neighbour_snrs",
    "seen_public", "seen_meshstl",
]
NEIGHBORINFO_APP = 71


class StandIns:
    """A stand-in integer per pseudonym, with the stored low byte as its low byte."""

    def __init__(self) -> None:
        self.ids: dict[str, int] = {}

    def of(self, pseudonym: str | None, low: int | None) -> int:
        if not pseudonym:
            return 0
        if pseudonym not in self.ids:
            self.ids[pseudonym] = ((len(self.ids) + 1) << 8) | int(low or 0)
        return self.ids[pseudonym]


def spike_status(row: dict[str, Any]) -> str:
    """The status analyze.py would have given: its "malformed" was a payload parse failure."""
    if row["decode_status"] == "malformed" and (row["portnum"] or 0) <= 0:
        return "unknown_port"
    return row["decode_status"]


def parsed_of(row: dict[str, Any], ids: StandIns) -> az.Parsed:
    topic = row["topic"]
    kind = topic_class(topic)
    status = spike_status(row)
    parsed = az.Parsed(
        row["received_at"].timestamp(), kind, az.topic_region(topic), status,
        "stripped" if status == "stripped" else "",
        gateway=ids.of(row["gateway_pseudonym"], row["gateway_low_byte"]),
        sender=ids.of(row["origin_pseudonym"], row["origin_low_byte"]),
        packet_id=row["packet_id"] or 0, relay=row["relay_node"] or 0,
        hop_limit=row["hop_limit"], hop_start=row["hop_start"] or 0, via_mqtt=row["via_mqtt"],
        rx_snr=row["rx_snr"] or 0.0, rx_rssi=row["rx_rssi"] or 0, portnum=row["portnum"],
    )
    parsed.root = topic_root(topic)
    has_place = row["latitude_i"] or row["longitude_i"]
    place = (row["latitude_i"], row["longitude_i"], row["precision_bits"])
    if has_place and row["position_source"] == "position":
        parsed.position = place
    if has_place and row["position_source"] == "mapreport":
        parsed.map_report = place
    if row["portnum"] == NEIGHBORINFO_APP and row["neighbour_pseudonyms"] is not None:
        parsed.neighbors = tuple(
            (ids.of(name, low), snr)
            for name, low, snr in zip(
                row["neighbour_pseudonyms"], row["neighbour_low_bytes"], row["neighbour_snrs"]
            )
        )
    return parsed


class Snapshot:
    """The snapshot's rows, read once, and its coverage."""

    def __init__(self, folder: Path) -> None:
        problems = verify_manifest(folder)
        if problems:
            raise SystemExit(f"snapshot differs from its manifest: {problems[:3]}")
        self.folder = folder
        self.manifest = read_manifest(folder)
        self.coverage = json.loads((folder / "coverage.json").read_text(encoding="utf-8"))
        self.ids = StandIns()
        self.rows = []
        for path in sorted((folder / "mqtt_message").glob("*.parquet")):
            self.rows += pq.read_table(path, columns=COLUMNS).to_pylist()

    def tally(self, broker: str, start_s: float, end_s: float, roots: Any = None) -> az.Tally:
        tally = az.Tally()
        flag = BROKER_FLAG[broker]
        for row in self.rows:
            at_s = row["received_at"].timestamp()
            if not row[flag] or not start_s <= at_s < end_s:
                continue
            if roots is not None and topic_root(row["topic"]) not in roots:
                continue
            tally.add(parsed_of(row, self.ids))
        for minute, low, high in self.coverage.get(broker, []):
            if high >= start_s and low < end_s:
                tally.minute_bounds[int(minute)] = (max(low, start_s), min(high, end_s))
        if tally.minute_bounds:
            tally.first_s = min(low for low, _ in tally.minute_bounds.values())
            tally.last_s = max(high for _, high in tally.minute_bounds.values())
        tally.counts[("kind", "e")] += 1  # analyze.main stops when counts is empty
        return tally


def bound(value: str | None, default: float) -> float:
    return datetime.fromisoformat(value).timestamp() if value else default


RUNS = {
    "public_full": ("public", None, None, None, None),
    "public_roots_mo_il_meshstl": ("public", None, None, "MO,IL,MeshSTL", None),
    "public_roots_mo_meshstl": ("public", None, None, "MO,MeshSTL", None),
    "public_shared_24h": ("public", *SHARED, None, None),
    "meshstl_24h": ("meshstl", *SHARED, None, None),
    "public_with_meshstl_positions": ("public", *SHARED, None, "meshstl"),
    "meshstl_with_public_positions": ("meshstl", *SHARED, None, "public"),
}


def run_analyze(snapshot: Snapshot, name: str, out_dir: Path) -> None:
    """One spike run through analyze.main, its loader replaced by the snapshot."""
    broker, start, end, roots, borrow = RUNS[name]
    az.load_capture = lambda data_dir, start_s, end_s, roots=None: snapshot.tally(
        str(data_dir), start_s, end_s, roots
    )
    argv = [str(SPIKE_DIR / "analyze.py"), broker, str(MSA_CSV)]
    argv += ["--start", start, "--end", end] if start else []
    argv += ["--roots", roots] if roots else []
    argv += ["--positions-from", borrow] if borrow else []
    sys.argv = argv
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        az.main()
    (out_dir / f"{name}.txt").write_text(buffer.getvalue(), encoding="utf-8")


def headline(snapshot: Snapshot, name: str) -> dict[str, Any]:
    """Link-hours a week and observed (relayers, gateways, pairs) per position rule, MSA."""
    broker, start, end, roots, borrow = RUNS[name]
    start_s, end_s = bound(start, -math.inf), bound(end, math.inf)
    tally = snapshot.tally(broker, start_s, end_s, frozenset(roots.split(",")) if roots else None)
    if borrow:
        with contextlib.redirect_stdout(io.StringIO()):
            az.borrow_positions(tally, snapshot.tally(borrow, start_s, end_s))
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
        "positions ignored": az.position_free_groups(tally, positions),
    }
    figures: dict[str, Any] = {"observed hours": f"{coverage.hours():.3f}"}
    for label, keys in measures.items():
        pairs = {(key[0], key[1]) for key in keys}
        week = coverage.rates(key[2] for key in keys).week_weighted
        relayers, gateways = len({p[0] for p in pairs}), len({p[1] for p in pairs})
        figures[label] = f"{week:.0f} ({len(keys)}; {relayers}, {gateways}, {len(pairs)})"
    funnel = thirty.funnel
    figures["funnel"] = " / ".join(
        str(funnel[step])
        for step in (
            "1 radio receipts with relay_node at area gateways",
            "3 lnk_01 relayer resolved",
            "5 lnk_02 both usable within the window",
            "6 lnk_05 500 m to 60 km",
        )
    )
    return figures


def text_counts(snapshot: Snapshot) -> dict[str, int]:
    """TEXT_MESSAGE_APP rows per broker flag (MeshSTL rows are its window already)."""
    counts: Counter[str] = Counter()
    for row in snapshot.rows:
        if row["portnum"] == 1:
            counts["public"] += int(row["seen_public"])
            counts["meshstl"] += int(row["seen_meshstl"])
    return dict(counts)


def copy_total(snapshot: Snapshot) -> int:
    paths = sorted((snapshot.folder / "mqtt_copy_count").glob("*.parquet"))
    return sum(sum(pq.read_table(path, columns=["copy_count"])["copy_count"].to_pylist()) for path in paths)


def merged_totals(snapshot: Snapshot) -> dict[str, dict[str, int]]:
    totals: dict[str, Counter[str]] = {}
    for row in snapshot.manifest["deliveries"]:
        part = totals.setdefault(row["broker"], Counter())
        for name in ("received", "stored", "counted", "merged_cross_broker", "merged_repeat"):
            part[name] += row[name]
    return {broker: dict(counts) for broker, counts in totals.items()}


# results.md figures, MSA, combined rule: week (observed; relayers, gateways, pairs).
EXPECTED = {
    "public_full": {
        "observed hours": "63.594",
        "keep 30 minutes, 500 m": "110 (42; 2, 4, 4)",
        "keep 30 minutes, 3 km": "107 (41; 1, 3, 3)",
        "stationarity window, 500 m": "168 (62; 4, 6, 6)",
        "stationarity window, 3 km": "162 (60; 3, 5, 5)",
        "gateway-only fallback": "852 (320; 23, 16, 28)",
        "positions ignored": "1194 (447; 36, 25, 46)",
        "funnel": "5760 / 4604 / 672 / 317",
    },
    "public_shared_24h": {
        "keep 30 minutes, 500 m": "177 (21; 1, 2, 2)",
        "keep 30 minutes, 3 km": "177 (21; 1, 2, 2)",
        "stationarity window, 500 m": "228 (29; 2, 2, 3)",
        "stationarity window, 3 km": "228 (29; 2, 2, 3)",
        "gateway-only fallback": "962 (128; 18, 10, 19)",
        "positions ignored": "1256 (170; 26, 15, 27)",
    },
    "meshstl_24h": {
        "keep 30 minutes, 500 m": "560 (74; 6, 6, 7)",
        "keep 30 minutes, 3 km": "281 (32; 4, 4, 5)",
        "stationarity window, 500 m": "617 (89; 6, 6, 7)",
        "stationarity window, 3 km": "309 (42; 4, 4, 5)",
        "gateway-only fallback": "1502 (200; 26, 14, 28)",
        "positions ignored": "2328 (332; 39, 22, 43)",
        "funnel": "3863 / 3184 / 919 / 719",
    },
    "public_with_meshstl_positions": {
        "keep 30 minutes, 500 m": "344",
        "keep 30 minutes, 3 km": "177",
        "stationarity window, 500 m": "399",
        "stationarity window, 3 km": "228",
        "gateway-only fallback": "1000",
        "positions ignored": "1299",
    },
    "meshstl_with_public_positions": {
        "keep 30 minutes, 500 m": "560",
        "keep 30 minutes, 3 km": "281",
        "stationarity window, 500 m": "617",
        "stationarity window, 3 km": "309",
        "gateway-only fallback": "1505",
        "positions ignored": "2328",
    },
    "public_roots_mo_il_meshstl": {
        "keep 30 minutes, 500 m": "104",
        "keep 30 minutes, 3 km": "101",
        "stationarity window, 500 m": "185",
        "stationarity window, 3 km": "178",
        "gateway-only fallback": "852",
        "positions ignored": "1194",
    },
    "public_roots_mo_meshstl": {
        "keep 30 minutes, 500 m": "104",
        "keep 30 minutes, 3 km": "101",
        "stationarity window, 500 m": "185",
        "stationarity window, 3 km": "178",
        "gateway-only fallback": "852",
        "positions ignored": "1194",
    },
}
EXPECTED_OTHER = {
    "TEXT_MESSAGE_APP rows, public": 142003,
    "TEXT_MESSAGE_APP rows, MeshSTL": 38,
    "internet copies, public (count table)": 74314521,
}


def compare(expected: str, reproduced: str) -> str:
    """Matches when the reproduced figure starts with the expected one (week, then detail)."""
    whole = reproduced.replace(" ", "") == expected.replace(" ", "")
    week_only = "(" not in expected and reproduced.split(" ")[0] == expected
    return "same" if whole or week_only else "DIFFERS"


def gate_table(snapshot: Snapshot) -> tuple[list[str], int]:
    lines = ["| run | figure | results.md | reproduced | difference |", "| --- | --- | --- | --- | --- |"]
    differs = 0
    for name, figures in EXPECTED.items():
        got = headline(snapshot, name)
        for label, expected in figures.items():
            verdict = compare(expected, str(got[label]))
            differs += verdict != "same"
            lines.append(f"| {name} | {label} | {expected} | {got[label]} | {verdict} |")
    texts = text_counts(snapshot)
    other = {
        "TEXT_MESSAGE_APP rows, public": texts.get("public", 0),
        "TEXT_MESSAGE_APP rows, MeshSTL": texts.get("meshstl", 0),
        "internet copies, public (count table)": copy_total(snapshot),
    }
    for label, expected_count in EXPECTED_OTHER.items():
        verdict = "same" if other[label] == expected_count else "DIFFERS"
        differs += verdict != "same"
        lines.append(f"| all | {label} | {expected_count} | {other[label]} | {verdict} |")
    for broker, totals in merged_totals(snapshot).items():
        for name in ("merged_cross_broker", "merged_repeat"):
            lines.append(f"| all | {name}, {broker} | not in results.md | {totals[name]} | reported |")
    return lines, differs


def main() -> None:
    folder, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    snapshot = Snapshot(folder)
    for name in RUNS:
        run_analyze(snapshot, name, out_dir)
    lines, differs = gate_table(snapshot)
    report = "\n".join(lines) + f"\n\nFigures that differ: {differs}\n"
    (out_dir / "gate.md").write_text(report, encoding="utf-8")
    print(report)
    raise SystemExit(1 if differs else 0)


if __name__ == "__main__":
    main()
