"""Casey's items 1 and 5 of 2026-11-13: one pass over the real-id archive, aggregates only.

Read R4. Every file of the public capture (without the 2026-10-28T20 fragment)
and of the MeshSTL capture is read once, in worker processes that hold the
real NODE_ID_HMAC_KEY in memory to compute each delivery_key as the
reduction does. From that one read:

- 1(a): the MeshSTL deliveries in its window are kept in memory, and with the
  public broker's first sighting of each delivery both brokers carried, the
  MeshSTL 24-hour tally is built with the two design rules and again with the
  shared rule. The link-hours that differ are traced receipt by receipt to
  what the shared rule changed, and their weekly weights are summed against
  the difference in the headline figure.
- 1(b): every delivery stored under a delivery_key that another delivery
  already holds is compared byte for byte (topic and payload) with the first
  one; failures are counted over all groups and over a seeded sample of 1 000.
- 1(c): analyze.py's figures with the payload-copies rule alone.
- 1(d): for merged repeats, the delay from the first to the last sighting,
  the members more than 30 minutes after the first, members with exactly the
  first one's received_at (a capture writing a record twice), and the share
  of repeats that arrive within 5 minutes after a gap of 10 seconds or more
  in the stream (a reconnect), against that window's share of observed time.
- 5: stored deliveries per broker with rx_snr 0.0 and rx_rssi set, with rx_snr
  and rx_rssi both 0, and with a nonzero rx_snr and rx_rssi 0.

It writes OUT_DIR/diagnose-2026-11-13.json and prints the same: counts,
seconds and figures only, never an id, topic, coordinate, payload or key.

Usage, from the repository root, Git Bash:

    uv run --frozen --with shapely==2.2.0 --with pyproj==3.8.0 python -I \
        docs/adr/0003-spike/diagnose_repeats.py OUT_DIR
"""

from __future__ import annotations

import argparse
import base64
import bisect
import copy
import json
import math
import random
import secrets
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import figures  # noqa: E402
import reference_design_rules as rules  # noqa: E402
from figures import RUNS, az  # noqa: E402

from ingester.decoder import CopyDelivery, StoredDelivery, decode_delivery  # noqa: E402
from ingester.finalise import merge_minutes  # noqa: E402
from ingester.reduce import ordered_results  # noqa: E402
from ingester.settings import get_ingester_settings  # noqa: E402

DATA = Path("C:/Users/Casey/mesh-terrain-data")
FRAGMENT = "capture-2026-10-28T20.jsonl"
GAP_S = 10.0
AFTER_GAP_S = 300.0
LATE_S = 1800.0
SAMPLE = 1000
SEED = 20261113
SHARED_S = figures.window_s(figures.SHARED)
# The copies-only tallies each file feeds: name -> (start_s, end_s, roots).
VARIANTS = {
    "public": {
        "full": (*figures.WHOLE, None),
        "roots MO,IL,MeshSTL": (*figures.WHOLE, frozenset({"MO", "IL", "MeshSTL"})),
        "roots MO,MeshSTL": (*figures.WHOLE, frozenset({"MO", "MeshSTL"})),
        "shared": (*SHARED_S, None),
    },
    "meshstl": {"shared": (*SHARED_S, None)},
}
COPIES_ONLY = frozenset({"copies"})
_KEY = b""


def _set_key(key: bytes) -> None:
    global _KEY
    _KEY = key


@dataclass
class Scan:
    """What one file gave: groups by delivery_key and the copies-only tallies."""

    broker: str
    path: str
    times: dict[bytes, list[float]] = field(default_factory=dict)
    first_bytes: dict[bytes, bytes] = field(default_factory=dict)
    labels: dict[bytes, str] = field(default_factory=dict)
    mismatched: set[bytes] = field(default_factory=set)
    snr: dict[str, set[bytes]] = field(default_factory=dict)
    copies_with_payload: int = 0
    gaps: list[tuple[float, float]] = field(default_factory=list)
    first_last: tuple[float, float] | None = None
    minutes: dict[int, tuple[float, float]] = field(default_factory=dict)
    tallies: dict[str, Any] = field(default_factory=dict)
    digests: dict[bytes, float] = field(default_factory=dict)
    digest_counts: Counter[bytes] = field(default_factory=Counter)
    digest_labels: dict[bytes, str] = field(default_factory=dict)
    kept: list[Any] = field(default_factory=list)


def label_of(row: dict[str, Any]) -> str:
    status = row["decode_status"]
    if status != "ok":
        return status
    return az.portnums_pb2.PortNum.Name(row["portnum"]) if row["portnum"] in PORT_NAMES else "ok"


PORT_NAMES = set(az.portnums_pb2.PortNum.values())


def note_stored(scan: Scan, at_s: float, topic: str, raw: bytes, decoded: Any) -> None:
    row = decoded.row
    key = bytes.fromhex(row["delivery_key"])
    content = len(topic.encode()).to_bytes(4, "big") + topic.encode() + raw
    scan.times.setdefault(key, []).append(at_s)
    first = scan.first_bytes.setdefault(key, content)
    if first != content:
        scan.mismatched.add(key)
    scan.labels.setdefault(key, label_of(row))
    snr, rssi = row["rx_snr"], row["rx_rssi"]
    case = None
    if snr == 0.0 and rssi is not None:
        case = "rx_snr 0.0, rx_rssi set"
    elif snr is None and rssi is None:
        case = "rx_snr 0.0, rx_rssi 0 (both null)"
    elif snr is not None and rssi is None:
        case = "rx_snr nonzero, rx_rssi 0"
    if case:
        scan.snr.setdefault(case, set()).add(key)
    digest = rules.digest(topic, raw)
    scan.digests[digest] = min(scan.digests.get(digest, at_s), at_s)
    scan.digest_counts[digest] += 1
    scan.digest_labels.setdefault(digest, label_of(row))


def scan_file(task: tuple[str, str, float, float]) -> Scan:
    """One read of one file: groups, byte checks, SNR cases, gaps and copies-only tallies."""
    path, broker, start_s, end_s = task
    scan = Scan(broker, path)
    variants = VARIANTS[broker]
    scan.tallies = {name: az.Tally() for name in variants}
    kept: dict[str, set[bytes]] = {name: set() for name in variants}
    stream: list[float] = []
    for item in rules.records(path, -math.inf, math.inf):
        at_s, kind, topic, record, _ = item
        for name, (low, high, roots) in variants.items():
            task_v = (path, low, high, roots, {}, {}, COPIES_ONLY)
            rules.tally_item(scan.tallies[name], kept[name], item, task_v)
        if not start_s <= at_s < end_s:
            continue
        stream.append(at_s)
        if broker == "meshstl" and SHARED_S[0] <= at_s < SHARED_S[1]:
            scan.kept.append(item)
        if kind not in {"e", "map"}:
            continue
        raw = base64.b64decode(record["payload_b64"])
        decoded = decode_delivery(topic, raw, datetime.fromtimestamp(at_s, UTC), _KEY)
        if isinstance(decoded, StoredDelivery):
            note_stored(scan, at_s, topic, raw, decoded)
        elif isinstance(decoded, CopyDelivery):
            scan.copies_with_payload += rules.classify(kind, raw) == "copy_with_payload"
    for name in variants:
        rules.finish_tally(scan.tallies[name])
    stream.sort()
    scan.gaps = gaps(stream)
    scan.first_last = (stream[0], stream[-1]) if stream else None
    scan.minutes = minute_bounds(stream)
    return scan


@dataclass
class Broker:
    """One broker's merged scans."""

    times: dict[bytes, list[float]] = field(default_factory=dict)
    first_bytes: dict[bytes, bytes] = field(default_factory=dict)
    labels: dict[bytes, str] = field(default_factory=dict)
    mismatched: set[bytes] = field(default_factory=set)
    snr: dict[str, set[bytes]] = field(default_factory=dict)
    copies_with_payload: int = 0
    gaps: list[tuple[float, float]] = field(default_factory=list)
    first_last: list[tuple[float, float]] = field(default_factory=list)
    minutes: dict[int, tuple[float, float]] = field(default_factory=dict)
    tallies: dict[str, Any] = field(default_factory=dict)
    digests: dict[bytes, tuple[float, str]] = field(default_factory=dict)
    digest_counts: Counter[bytes] = field(default_factory=Counter)
    digest_labels: dict[bytes, str] = field(default_factory=dict)
    kept: dict[str, list[Any]] = field(default_factory=dict)

    def absorb(self, scan: Scan) -> None:
        for key, at in scan.times.items():
            self.times.setdefault(key, []).extend(at)
        for key, content in scan.first_bytes.items():
            if self.first_bytes.setdefault(key, content) != content:
                self.mismatched.add(key)
        for key, label in scan.labels.items():
            self.labels.setdefault(key, label)
        self.mismatched |= scan.mismatched
        for case, keys in scan.snr.items():
            self.snr.setdefault(case, set()).update(keys)
        self.copies_with_payload += scan.copies_with_payload
        self.gaps += scan.gaps
        if scan.first_last is not None:
            self.first_last.append(scan.first_last)
        merge_minutes(self.minutes, scan.minutes)
        for name, tally in scan.tallies.items():
            self.tallies.setdefault(name, az.Tally()).merge(tally)
        for digest, at_s in scan.digests.items():
            known = self.digests.get(digest)
            if known is None or at_s < known[0]:
                self.digests[digest] = (at_s, scan.path)
        self.digest_counts.update(scan.digest_counts)
        for digest, label in scan.digest_labels.items():
            self.digest_labels.setdefault(digest, label)
        self.kept[scan.path] = scan.kept


def read_broker(broker: str, paths: list[str], window: tuple[float, float], key: bytes) -> Broker:
    merged, started = Broker(), time.monotonic()
    tasks = [[(path, broker, *window)] for path in paths]
    with ProcessPoolExecutor(rules.WORKERS, initializer=_set_key, initargs=(key,)) as pool:
        for [scan] in ordered_results(pool, scan_file, tasks, limit=rules.WORKERS):
            merged.absorb(scan)
    print(
        f"read {broker}: {len(paths)} files in {time.monotonic() - started:.0f} s", file=sys.stderr
    )
    return merged


def byte_check(brokers: dict[str, Broker]) -> dict[str, Any]:
    """1(b): groups whose members differ in bytes, over all groups and a seeded sample."""
    found: dict[str, Any] = {}
    for name, data in brokers.items():
        groups = sorted(key for key, at in data.times.items() if len(at) > 1)
        sample = random.Random(SEED).sample(groups, min(SAMPLE, len(groups)))
        found[name] = {
            "merged groups": len(groups),
            "members in merged groups": sum(len(data.times[key]) for key in groups),
            "groups whose bytes differ, all": len(data.mismatched),
            "sample size": len(sample),
            "members in the sample": sum(len(data.times[key]) for key in sample),
            "groups whose bytes differ, sample": sum(key in data.mismatched for key in sample),
        }
    return found


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))] if ordered else 0.0


def gaps(stream: list[float]) -> list[tuple[float, float]]:
    ordered = sorted(stream)
    return [(a, b) for a, b in zip(ordered, ordered[1:], strict=False) if b - a >= GAP_S]


def all_gaps(data: Broker) -> list[tuple[float, float]]:
    """Gaps inside files and between one file's last record and the next file's first."""
    bounds = sorted(data.first_last)
    between = [(a[1], b[0]) for a, b in zip(bounds, bounds[1:], strict=False)]
    return sorted(data.gaps + [gap for gap in between if gap[1] - gap[0] >= GAP_S])


def after_gap(at_s: float, ends: list[float]) -> bool:
    index = bisect.bisect_right(ends, at_s) - 1
    return index >= 0 and at_s - ends[index] <= AFTER_GAP_S


def delays(data: Broker) -> dict[str, Any]:
    """1(d) for one broker."""
    spans, buckets, late_by_label = [], Counter(), Counter()
    ends = sorted(end for _, end in all_gaps(data))
    repeat_after_gap = repeats = late = late_groups = identical = 0
    for key, at in data.times.items():
        if len(at) < 2:
            continue
        at = sorted(at)
        spans.append(at[-1] - at[0])
        late_groups += at[-1] - at[0] > LATE_S
        for member in at[1:]:
            repeats += 1
            delay = member - at[0]
            identical += delay == 0.0
            late += delay > LATE_S
            late_by_label[data.labels[key]] += delay > LATE_S
            repeat_after_gap += after_gap(member, ends)
            for bound in (0.0, 1.0, 10.0, 60.0, LATE_S, math.inf):
                if delay <= bound:
                    buckets[f"<= {bound:g} s"] += 1
                    break
    observed = az.Coverage.from_minutes(data.minutes)
    gap_window_h = sum(observed.overlap_h(end, end + AFTER_GAP_S) for end in ends)
    return {
        "groups merged": len(spans),
        "repeat deliveries": repeats,
        "first to last, median s": round(statistics.median(spans), 3) if spans else 0,
        "first to last, 90th percentile s": round(percentile(spans, 0.9), 3),
        "first to last, maximum s": round(max(spans, default=0.0), 3),
        "repeat deliveries more than 30 minutes after the first": late,
        "groups with a member more than 30 minutes after the first": late_groups,
        "late repeats by decode status or portnum": dict(late_by_label.most_common(12)),
        "repeat deliveries by delay from the first": dict(buckets),
        "repeat deliveries with exactly the first one's received_at": identical,
        "gaps of 10 s or more in the stream": len(ends),
        "repeats within 5 minutes after such a gap": repeat_after_gap,
        "share of repeats within 5 minutes after a gap": round(
            repeat_after_gap / max(repeats, 1), 4
        ),
        "share of observed time within 5 minutes after a gap": round(
            gap_window_h / max(observed.hours(), 1e-9), 4
        ),
        "repeat deliveries by decode status or portnum": dict(
            Counter(
                data.labels[key] for key, at in data.times.items() for _ in range(len(at) - 1)
            ).most_common(12)
        ),
    }


def minute_bounds(stream: list[float]) -> dict[int, tuple[float, float]]:
    bounds: dict[int, tuple[float, float]] = {}
    for at_s in stream:
        minute = int(at_s // 60)
        low, high = bounds.get(minute, (at_s, at_s))
        bounds[minute] = (min(low, at_s), max(high, at_s))
    return bounds


def copies_only(public: Broker, meshstl: Broker) -> dict[str, dict[str, str]]:
    """1(c): analyze.py's figures with the payload-copies rule alone."""
    tallies: dict[tuple[Any, ...], Any] = {}
    for broker, data in (("public", public), ("meshstl", meshstl)):
        for name, (low, high, roots) in VARIANTS[broker].items():
            tallies[(broker, low, high, roots)] = data.tallies[name]

    def load(broker: str, start_s: float, end_s: float, roots: Any) -> Any:
        return copy.deepcopy(tallies[(broker, start_s, end_s, roots)])

    return {name: figures.headline(load, name) for name in RUNS}


def meshstl_tally(public: Broker, meshstl: Broker, ruleset: frozenset[str]) -> Any:
    """The MeshSTL 24-hour tally under the rules, from the records kept in memory."""
    repeated = {d: at for d, at in meshstl.digests.items() if meshstl.digest_counts[d] > 1}
    shift = {
        d: public.digests[d][0]
        for d, (at_s, _) in meshstl.digests.items()
        if d in public.digests and public.digests[d][0] < at_s
    }
    total = az.Tally()
    for path, items in sorted(meshstl.kept.items()):
        task = (path, *SHARED_S, None, repeated, shift, ruleset)
        total.merge(rules.tally_items(items, task))
    return total


def resolved(tally: Any, positions: Any) -> dict[tuple[int, int, int], tuple[float, int, bool]]:
    """Each radio receipt at an area gateway: (time, relayer under the combined rule, lnk_06)."""
    gateways = positions.any_nodes & set(tally.heard)
    candidates = {gateway: az.by_low_byte(tally.heard[gateway]) for gateway in gateways}
    neighbours = az.neighbors_by_reporter(tally)
    found = {}
    for key, (at_s, relay, signal_ok, _) in tally.receipts.items():
        if key[0] in gateways:
            relayer = az.resolve_all(key[1], relay, candidates[key[0]], neighbours.get(key[0], {}))
            found[key] = (at_s, relayer["combined"], signal_ok)
    return found


@dataclass
class Side:
    """One tally's link-hours, receipts and positions for the 1(a) trace."""

    tally: Any
    positions: Any = None
    result: Any = None
    receipts: dict[Any, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        msa = az.Area("the MSA (CBSA 41180)", az.load_area(figures.MSA_CSV, {"41180"}))
        self.positions = az.area_positions(self.tally, msa)
        self.result = az.apply_rules(self.tally, self.positions)["combined"]
        self.receipts = resolved(self.tally, self.positions)

    def keys(self) -> set[tuple[int, int, int]]:
        return set(self.result.link_hours(az.MIN_DISTANCE_M))


def receipt_category(other: Side, key: Any, link: tuple[int, int, int]) -> tuple[str, float]:
    """What happened in the other tally to one receipt of a link-hour, and its time shift."""
    relayer, gateway, hour = link
    if key not in other.receipts:
        return "receipt not in the other tally", 0.0
    at_s, other_relayer, _ = other.receipts[key]
    if other_relayer != relayer:
        return "relayer resolved differently", at_s
    if int(at_s // 3600) != hour:
        return "receipt moved to another clock hour", at_s
    if other.positions.near(relayer, at_s, az.POSITION_WINDOW_S) is None:
        return "relayer has no usable position within 30 minutes", at_s
    if other.positions.near(gateway, at_s, az.POSITION_WINDOW_S) is None:
        return "gateway has no usable position within 30 minutes", at_s
    return "still in the link-hour", at_s


def positions_near(side: Side, node: int, times: list[float]) -> list[float]:
    rows = side.positions.usable.get(node, [])
    return [row[0] for row in rows if any(abs(row[0] - at) <= az.POSITION_WINDOW_S for at in times)]


def trace_link(
    have: Side, other: Side, link: tuple[int, int, int], coverage: Any
) -> dict[str, Any]:
    """Why one link-hour is in one tally and not the other, in counts and seconds."""
    relayer, gateway, hour = link
    members = [
        (key, at_s)
        for key, (at_s, found, ok) in have.receipts.items()
        if key[0] == gateway and found == relayer and ok and int(at_s // 3600) == hour
    ]
    categories: Counter[str] = Counter()
    shifts = []
    for key, at_s in members:
        category, other_at = receipt_category(other, key, link)
        categories[category] += 1
        if other_at and other_at != at_s:
            shifts.append(round(other_at - at_s, 3))
    times = [at_s for _, at_s in members]
    distances = other.result.windows.get(link, [])
    return {
        "hour (UTC)": f"{datetime.fromtimestamp(hour * 3600, UTC):%Y-%m-%dT%H}",
        "day class": az.day_class(hour),
        "week contribution": round(coverage.rates([hour]).week_weighted, 4),
        "packets here / in the other tally": [len(have.result.windows[link]), len(distances)],
        "median 500 m or more in the other tally": bool(distances)
        and statistics.median(distances) >= az.MIN_DISTANCE_M,
        "receipts by what happened in the other tally": dict(categories),
        "receipt time shifts, s": sorted(shifts),
        "usable position samples near the receipts, relayer here / other": [
            len(positions_near(have, relayer, times)),
            len(positions_near(other, relayer, times)),
        ],
        "usable position samples near the receipts, gateway here / other": [
            len(positions_near(have, gateway, times)),
            len(positions_near(other, gateway, times)),
        ],
    }


def explain_shared(public: Broker, meshstl: Broker, references: dict[str, Any]) -> dict[str, Any]:
    """1(a): the MeshSTL 24-hour figure with the two rules against the same with the shared rule."""
    two = Side(meshstl_tally(public, meshstl, frozenset({"copies", "repeats"})))
    three = Side(meshstl_tally(public, meshstl, frozenset({"copies", "repeats", "shared"})))
    coverage = two.tally.coverage()
    weeks = [coverage.rates(k[2] for k in side.keys()).week_weighted for side in (two, three)]
    lost = [trace_link(two, three, link, coverage) for link in sorted(two.keys() - three.keys())]
    gained = [trace_link(three, two, link, coverage) for link in sorted(three.keys() - two.keys())]
    accounted = sum(i["week contribution"] for i in lost) - sum(
        i["week contribution"] for i in gained
    )
    shifted = {
        d: public.digests[d][0] - at_s
        for d, (at_s, _) in meshstl.digests.items()
        if d in public.digests and public.digests[d][0] < at_s
    }
    unexplained = [
        item
        for item in lost + gained
        if set(item["receipts by what happened in the other tally"]) == {"still in the link-hour"}
        and item["packets here / in the other tally"][0]
        == item["packets here / in the other tally"][1]
    ]
    return {
        "figures with the two rules, recomputed": figures.headline(
            lambda *_: copy.deepcopy(two.tally), "meshstl_24h"
        ),
        "figures with the shared rule, recomputed": figures.headline(
            lambda *_: copy.deepcopy(three.tally), "meshstl_24h"
        ),
        "reference (c), two rules": references["c"]["runs"]["meshstl_24h"]["figures"],
        "reference, shared rule": references["shared"]["runs"]["meshstl_24h"]["figures"],
        "keep 30 minutes at 500 m, week, two rules / shared": [round(w, 4) for w in weeks],
        "difference": round(weeks[0] - weeks[1], 4),
        "sum of the changed link-hours' week contributions": round(accounted, 4),
        "link-hours lost with the shared rule": lost,
        "link-hours gained with the shared rule": gained,
        "changed link-hours with no traced cause": len(unexplained),
        "MeshSTL deliveries the public broker sighted first": len(shifted),
        "their shift to the public time, s: median, 90th percentile, maximum": [
            round(statistics.median(-s for s in shifted.values()), 3) if shifted else 0,
            round(percentile([-s for s in shifted.values()], 0.9), 3),
            round(max((-s for s in shifted.values()), default=0.0), 3),
        ],
        "their decode status or portnum": dict(
            Counter(meshstl.digest_labels[d] for d in shifted).most_common()
        ),
    }


def snr_counts(brokers: dict[str, Broker]) -> dict[str, dict[str, int]]:
    """5: stored deliveries per broker in each SNR case, counted once per delivery_key."""
    return {
        name: {case: len(keys) for case, keys in data.snr.items()} for name, data in brokers.items()
    }


def load_references() -> dict[str, Any]:
    folder = figures.SPIKE_DIR / "reference"
    names = {
        "b": "reference-without-fragment.json",
        "c": "reference-design-rules-copies-repeats.json",
        "shared": "reference-design-rules-copies-repeats-shared.json",
    }
    return {
        key: json.loads((folder / name).read_text(encoding="utf-8")) for key, name in names.items()
    }


def rule_split(copies: dict[str, dict[str, str]], references: dict[str, Any]) -> dict[str, Any]:
    """1(c): each figure as (b), copies rule alone, and (c); a figure that moves says which rule."""
    split: dict[str, Any] = {}
    for run, found in copies.items():
        for label, value in found.items():
            b = references["b"]["runs"][run]["figures"][label]
            c = references["c"]["runs"][run]["figures"][label]
            if b != c or b != value:
                split.setdefault(run, {})[label] = {"b": b, "copies rule alone": value, "c": c}
    return split


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out_dir", type=Path)
    # For a smoke run on the synthetic fixtures only; the real run takes the defaults.
    parser.add_argument("--public", type=Path, default=DATA / "spike-capture")
    parser.add_argument("--meshstl", type=Path, default=DATA / "meshstl-capture")
    parser.add_argument("--meshstl-window", nargs=2, default=list(rules.MESHSTL_WINDOW))
    parser.add_argument("--throwaway-key", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    references = load_references()
    key = secrets.token_bytes(32) if args.throwaway_key else get_ingester_settings().hmac_key()
    public_paths = [p for p in az.capture_paths(args.public) if Path(p).name != FRAGMENT]
    meshstl_paths = az.capture_paths(args.meshstl)
    window = figures.window_s(tuple(args.meshstl_window))
    meshstl = read_broker("meshstl", meshstl_paths, window, key)
    public = read_broker("public", public_paths, figures.WHOLE, key)
    del key
    brokers = {"public": public, "meshstl": meshstl}
    copies = copies_only(public, meshstl)
    result = {
        "files read": {"public": len(public_paths), "meshstl": len(meshstl_paths)},
        "1(a) shared rule on MeshSTL": explain_shared(public, meshstl, references),
        "1(b) byte check": byte_check(brokers),
        "1(c) copies rule alone, figures": copies,
        "1(c) figures that move, (b) / copies rule alone / (c)": rule_split(copies, references),
        "1(c) copies with a payload": {n: d.copies_with_payload for n, d in brokers.items()},
        "1(d) repeats": {name: delays(data) for name, data in brokers.items()},
        "5 SNR cases, stored deliveries": snr_counts(brokers),
    }
    text = json.dumps(result, indent=1, sort_keys=True)
    (out_dir / "diagnose-2026-11-13.json").write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
