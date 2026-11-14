"""Reproduction gate: every ADR 0003 link figure from the pseudonymised snapshot alone.

Reads only the snapshot folder (after checking every file against its
manifest): the delivery rows, the count tables, coverage.json,
coverage_roots.json and the manifest. Each pseudonym becomes a stand-in
integer whose low byte is the stored low byte, so analyze.py's link rules run
unchanged on the rows: relay_node resolution matches low bytes exactly as it
did on real ids. The broker flags pick each broker's rows; the coverage files
give each broker's observed minutes, per regional topic root for the --roots
runs. The real-id archive is never opened.

Casey's gate rule of 2026-11-13: four figures per broker and run, (a)
results.md, (b) analyze.py on the archive without the 2026-10-28T20 fragment,
(c) the same with the four design rules (internet copies that kept a payload
counted, not used; identical repeats merged; a delivery both brokers carried
timed at its first sighting on either broker, added by Casey on 2026-11-13; a
delivery on an `e` or `map` topic whose envelope does not parse or whose sender
is 0 counted as `unparsed`, not used, added on 2026-11-14), from
reference_design_rules.py, and (d) this snapshot. (b) and (c)
are aggregates committed under `reference/`. The gate passes when (c) equals
(d) in every figure, and the exit code says so: 0 for a pass, 1 for any
difference. It also reports, from
the snapshot, merged_cross_broker and merged_repeat per broker, the
decrypt_failed deliveries and rows made by the portnum rule beside the spike's
wrong-key count, and the rows with an SNR of 0.0.

Writes, under OUT_DIR, analyze.py's full output of the seven runs on the
snapshot and gate.md. Aggregates only: no pseudonym, stand-in id, topic or
coordinate is printed.

Usage, from the repository root, Git Bash (shapely and pyproj are the spike
Dockerfile's pins and enter no lock file):

    uv run --frozen --with shapely==2.2.0 --with pyproj==3.8.0 python -I \
        docs/adr/0003-spike/reproduce_from_snapshot.py \
        C:/Users/Casey/mesh-terrain-data/snapshots/meshtastic-capture-2026-10-22 OUT_DIR
"""

from __future__ import annotations

import contextlib
import io
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import figures  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402
from figures import RUNS, az  # noqa: E402

from ingester.snapshot import read_manifest, verify_manifest  # noqa: E402
from ingester.topics import topic_class, topic_root  # noqa: E402

REFERENCE_DIR = figures.SPIKE_DIR / "reference"
BROKER_FLAG = {"public": "seen_public", "meshstl": "seen_meshstl"}
COLUMNS = [
    "received_at", "topic", "decode_status", "gateway_pseudonym", "gateway_low_byte",
    "origin_pseudonym", "origin_low_byte", "packet_id", "relay_node", "hop_start", "hop_limit",
    "via_mqtt", "rx_snr", "rx_rssi", "portnum", "position_source", "latitude_i", "longitude_i",
    "precision_bits", "neighbour_pseudonyms", "neighbour_low_bytes", "neighbour_snrs",
    "seen_public", "seen_meshstl",
]  # fmt: skip
NEIGHBORINFO_APP = 71
TEXT_MESSAGE_APP = 1


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


def snr_of(row: dict[str, Any]) -> float:
    """analyze.py's reading of rx_snr.

    A null SNR lands only with a null RSSI (rx_rssi 0), which analyze.py's
    lnk_06 test rejects on RSSI before it looks at SNR; NaN fails every range
    test, so the outcome is the one analyze.py gave the 0.0 it read.
    """
    return math.nan if row["rx_snr"] is None else float(row["rx_snr"])


def parsed_of(row: dict[str, Any], ids: StandIns) -> Any:
    topic = row["topic"]
    status = spike_status(row)
    parsed = az.Parsed(
        row["received_at"].timestamp(), topic_class(topic), az.topic_region(topic), status,
        "stripped" if status == "stripped" else "",
        gateway=ids.of(row["gateway_pseudonym"], row["gateway_low_byte"]),
        sender=ids.of(row["origin_pseudonym"], row["origin_low_byte"]),
        packet_id=row["packet_id"] or 0, relay=row["relay_node"] or 0,
        hop_limit=row["hop_limit"], hop_start=row["hop_start"] or 0, via_mqtt=row["via_mqtt"],
        rx_snr=snr_of(row), rx_rssi=row["rx_rssi"] or 0, portnum=row["portnum"],
    )  # fmt: skip
    parsed.root = topic_root(topic)
    # A Position field the sender left unset lands as null; analyze.py read the protobuf
    # default, 0, so the same 0 goes back in. That applies inside this gate only, to match
    # the spike (Casey, 2026-11-14): nothing downstream reads a missing coordinate as 0,
    # and staging keeps it null.
    has_place = row["latitude_i"] or row["longitude_i"]
    place = (row["latitude_i"] or 0, row["longitude_i"] or 0, row["precision_bits"] or 0)
    if has_place and row["position_source"] == "position":
        parsed.position = place
    if has_place and row["position_source"] == "mapreport":
        parsed.map_report = place
    if row["portnum"] == NEIGHBORINFO_APP and row["neighbour_pseudonyms"] is not None:
        parsed.neighbors = tuple(
            (ids.of(name, low), snr)
            for name, low, snr in zip(
                row["neighbour_pseudonyms"],
                row["neighbour_low_bytes"],
                row["neighbour_snrs"],
                strict=True,
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
        roots = (folder / "coverage_roots.json").read_text(encoding="utf-8")
        self.coverage_roots = json.loads(roots)
        self.ids = StandIns()
        self.rows: list[dict[str, Any]] = []
        for path in sorted((folder / "mqtt_message").glob("*.parquet")):
            self.rows += pq.read_table(path, columns=COLUMNS).to_pylist()

    def minutes(self, broker: str, roots: Any) -> list[list[float]]:
        """Observed minutes of a broker, under the given regional roots only when roots is set."""
        if roots is None:
            return self.coverage.get(broker, [])
        bounds: dict[int, tuple[float, float]] = {}
        for root in roots:
            for minute, low, high in self.coverage_roots.get(broker, {}).get(root, []):
                known = bounds.get(int(minute), (low, high))
                bounds[int(minute)] = (min(known[0], low), max(known[1], high))
        return [[minute, low, high] for minute, (low, high) in sorted(bounds.items())]

    def tally(self, broker: str, start_s: float, end_s: float, roots: Any = None) -> Any:
        tally = az.Tally()
        flag = BROKER_FLAG[broker]
        for row in self.rows:
            at_s = row["received_at"].timestamp()
            if not row[flag] or not start_s <= at_s < end_s:
                continue
            if roots is not None and topic_root(row["topic"]) not in roots:
                continue
            tally.add(parsed_of(row, self.ids))
        # A minute cut by the window keeps its bounds clipped to the window; analyze.py
        # takes the first and last record inside it, a difference of under a second.
        for minute, low, high in self.minutes(broker, roots):
            if high >= start_s and low < end_s:
                tally.minute_bounds[int(minute)] = (max(low, start_s), min(high, end_s))
        if tally.minute_bounds:
            tally.first_s = min(low for low, _ in tally.minute_bounds.values())
            tally.last_s = max(high for _, high in tally.minute_bounds.values())
        # analyze.main stops when counts is empty; a key no report prints.
        tally.counts[("gate", "rows")] += 1
        return tally


def run_analyze(snapshot: Snapshot, name: str, out_dir: Path) -> None:
    """One spike run through analyze.main, its loader replaced by the snapshot."""
    broker, start, end, roots, borrow = RUNS[name]
    az.load_capture = lambda data_dir, start_s, end_s, roots=None: snapshot.tally(
        str(data_dir), start_s, end_s, roots
    )
    argv = [str(figures.SPIKE_DIR / "analyze.py"), broker, str(figures.MSA_CSV)]
    argv += ["--start", start, "--end", end] if start and end else []
    argv += ["--roots", roots] if roots else []
    argv += ["--positions-from", borrow] if borrow else []
    sys.argv = argv
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        az.main()
    (out_dir / f"{name}.txt").write_text(buffer.getvalue(), encoding="utf-8")


def snapshot_counts(snapshot: Snapshot) -> dict[str, dict[str, int]]:
    """Per broker: text rows, counted internet copies, stored deliveries and merged repeats."""
    counts: dict[str, Counter[str]] = {broker: Counter() for broker in BROKER_FLAG}
    for row in snapshot.rows:
        for broker, flag in BROKER_FLAG.items():
            counts[broker]["TEXT_MESSAGE_APP rows"] += int(
                row[flag] and row["portnum"] == TEXT_MESSAGE_APP
            )
    paths = sorted((snapshot.folder / "mqtt_copy_count").glob("*.parquet"))
    copies = sum(sum(pq.read_table(p, columns=["copy_count"])[0].to_pylist()) for p in paths)
    counts["public"]["internet copies counted"] = copies
    for row in snapshot.manifest["deliveries"]:
        part = counts[row["broker"]]
        part["stored deliveries"] += row["stored"] + row["merged_cross_broker"]
        part["stored deliveries"] += row["merged_repeat"]
        part["repeats merged"] += row["merged_repeat"]
    return {broker: dict(found) for broker, found in counts.items()}


def reference_counts(reference: dict[str, Any]) -> dict[str, dict[str, int | None]]:
    """The same counts from a reference run; copies kept as counts are added back for (c)."""
    found: dict[str, dict[str, int | None]] = {}
    for broker, counts in reference["counts"].items():
        rules = reference.get("rules", {}).get(broker, {})
        found[broker] = {
            "TEXT_MESSAGE_APP rows": counts["TEXT_MESSAGE_APP envelopes"],
            "stored deliveries": rules.get("stored deliveries"),
            "repeats merged": rules.get("repeats merged"),
        }
        if broker == "public":
            kept = rules.get("copies with a payload, counted not used", 0)
            found[broker]["internet copies counted"] = counts["internet copies"] + kept
    return found


# (a) results.md, MSA, combined rule: week (observed; relayers, gateways, pairs).
RESULTS_MD: dict[str, dict[str, str]] = {
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
        "observed hours": "24.000",
        "keep 30 minutes, 500 m": "177 (21; 1, 2, 2)",
        "keep 30 minutes, 3 km": "177 (21; 1, 2, 2)",
        "stationarity window, 500 m": "228 (29; 2, 2, 3)",
        "stationarity window, 3 km": "228 (29; 2, 2, 3)",
        "gateway-only fallback": "962 (128; 18, 10, 19)",
        "positions ignored": "1256 (170; 26, 15, 27)",
    },
    "meshstl_24h": {
        "observed hours": "23.998",
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
RESULTS_MD_COUNTS = {
    "public": {"TEXT_MESSAGE_APP rows": 142003, "internet copies counted": 74314521},
    "meshstl": {"TEXT_MESSAGE_APP rows": 38},
}
REFERENCES = {
    "b": "reference-without-fragment.json",
    "c": "reference-design-rules-copies-repeats-shared-unparsed.json",
}


def load_references(directory: Path) -> dict[str, dict[str, Any]]:
    """(b), (c) and, when it exists, (c) with the shared rule."""
    found = {}
    for name, file in REFERENCES.items():
        path = directory / file
        if path.is_file():
            found[name] = json.loads(path.read_text(encoding="utf-8"))
    if "b" not in found or "c" not in found:
        raise SystemExit(f"reference figures (b) and (c) are missing from {directory}")
    return found


def cell(value: Any) -> str:
    return "" if value is None else str(value)


def figure_rows(snapshot: Snapshot, references: dict[str, Any]) -> tuple[list[str], int]:
    """One table row per run and figure; the count of figures where (c) and (d) differ."""
    lines, differs = [], 0
    for name in RUNS:
        found = figures.headline(snapshot.tally, name)
        for label, value in found.items():
            b, c = (references[key]["runs"][name]["figures"][label] for key in ("b", "c"))
            verdict = "match" if c == value else "DIFFERS"
            differs += verdict != "match"
            row = [name, label, RESULTS_MD.get(name, {}).get(label), b, c, value, verdict]
            lines.append("| " + " | ".join(cell(item) for item in row) + " |")
    return lines, differs


def count_rows(snapshot: Snapshot, references: dict[str, Any]) -> tuple[list[str], int]:
    lines, differs = [], 0
    b, c = reference_counts(references["b"]), reference_counts(references["c"])
    for broker, counts in snapshot_counts(snapshot).items():
        for label, value in counts.items():
            verdict = "match" if c[broker].get(label) == value else "DIFFERS"
            differs += verdict != "match"
            row = [broker, label, RESULTS_MD_COUNTS[broker].get(label)]
            row += [b[broker].get(label), c[broker].get(label), value, verdict]
            lines.append("| " + " | ".join(cell(item) for item in row) + " |")
    return lines, differs


def report_rows(snapshot: Snapshot, references: dict[str, Any]) -> list[str]:
    """Reported, not compared: merged counts, the portnum rule, SNR 0.0, rows on both brokers."""
    merged: dict[str, Counter[str]] = {broker: Counter() for broker in BROKER_FLAG}
    for row in snapshot.manifest["deliveries"]:
        for name in ("merged_cross_broker", "merged_repeat"):
            merged[row["broker"]][name] += row[name]
    rule = snapshot.manifest["decrypt_failed_portnum_rule"]
    lines = []
    for broker in BROKER_FLAG:
        for name in ("merged_cross_broker", "merged_repeat"):
            lines.append(f"| {broker} | {name} | {merged[broker][name]} |")
        lines.append(
            f"| {broker} | decrypt_failed by the portnum rule, deliveries / rows with the flag "
            f"| {rule['deliveries'][broker]} / {rule['rows'][BROKER_FLAG[broker]]} |"
        )
        spike = references["b"]["counts"][broker]["decrypt_failed: wrong_key"]
        lines.append(f"| {broker} | spike decrypt_failed wrong_key, (b) | {spike} |")
        lines += snr_rows(snapshot, broker)
    shared = references["c"].get("shared_rows", {})
    lines += [f"| both | rows both brokers carried, {k} | {v} |" for k, v in shared.items()]
    lines += both_ends_pairs(snapshot)
    return lines


def stationary_pairs(snapshot: Snapshot, name: str) -> set[tuple[int, int]]:
    """(relayer, gateway) pairs of a run's stationarity-window link-hours at 500 m, MSA."""
    tally = figures.run_tallies(snapshot.tally, name)
    msa = az.Area("the MSA (CBSA 41180)", az.load_area(figures.MSA_CSV, {"41180"}))
    positions = az.area_positions(tally, msa)
    result = az.apply_rules(tally, positions, stationary=True)["combined"]
    return {(key[0], key[1]) for key in result.link_hours(az.MIN_DISTANCE_M)}


def both_ends_pairs(snapshot: Snapshot) -> list[str]:
    """The union of both-ends-positioned pairs (docs/measurements-2026-11-08.md: 8 in the spike).

    Stand-ins are shared by both brokers' tallies, so a pair both carried counts once.
    """
    public = stationary_pairs(snapshot, "public_full")
    meshstl = stationary_pairs(snapshot, "meshstl_24h")
    return [
        f"| both | both-ends pairs, stationarity at 500 m: public / MeshSTL / shared / union "
        f"| {len(public)} / {len(meshstl)} / {len(public & meshstl)} / {len(public | meshstl)} |"
    ]


def snr_rows(snapshot: Snapshot, broker: str) -> list[str]:
    flag = BROKER_FLAG[broker]
    zero_with_rssi = sum(
        1 for row in snapshot.rows if row[flag] and row["rx_snr"] == 0.0 and row["rx_rssi"]
    )
    null_without_rssi = sum(
        1 for row in snapshot.rows if row[flag] and row["rx_snr"] is None and not row["rx_rssi"]
    )
    return [
        f"| {broker} | rx_snr 0.0 and rx_rssi set | {zero_with_rssi} |",
        f"| {broker} | rx_snr null and rx_rssi null | {null_without_rssi} |",
    ]


def gate_report(snapshot: Snapshot, references: dict[str, Any]) -> tuple[str, int]:
    header = (
        "| run | figure | (a) results.md | (b) raw | (c) raw, four design rules | (d) snapshot "
    )
    header += "| (c) and (d) |"
    figure_lines, figure_differs = figure_rows(snapshot, references)
    count_lines, count_differs = count_rows(snapshot, references)
    lines = [header, "| --- " * 7 + "|", *figure_lines, ""]
    lines += [
        "| broker | count | (a) results.md | (b) raw | (c) raw, four design rules | (d) snapshot "
    ]
    lines[-1] += "| (c) and (d) |"
    lines += ["| --- " * 7 + "|", *count_lines, "", "| broker | reported | snapshot |"]
    lines += ["| --- | --- | --- |", *report_rows(snapshot, references)]
    differs = figure_differs + count_differs
    verdict = "PASSES" if not differs else f"FAILS: {differs} figures where (c) and (d) differ"
    return "\n".join(lines) + f"\n\nGate {verdict}.\n", differs


def main() -> None:
    folder, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    directory = Path(sys.argv[3]) if len(sys.argv) > 3 else REFERENCE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    references = load_references(directory)
    snapshot = Snapshot(folder)
    for name in RUNS:
        run_analyze(snapshot, name, out_dir)
    report, differs = gate_report(snapshot, references)
    (out_dir / "gate.md").write_text(report, encoding="utf-8")
    print(report)
    raise SystemExit(1 if differs else 0)


if __name__ == "__main__":
    main()
