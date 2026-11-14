"""Read R9 (Casey, 2026-11-14): sender-0 text packets, the retain flag and repeats after subscribes.

One pass over the files R1 and R4 read: the 64 public hours (the 2026-10-28T20
fragment is read for its JSON keys only) and the MeshSTL capture in its window.
It prints and writes aggregates only: counts, seconds and labels, never a node
id, position, text, topic or payload. No HMAC key is read: repeats are grouped
by a hash of topic and payload held in memory, which groups exactly as
delivery_key does.

- The JSON keys of every record, to say whether the archive kept the MQTT
  retain flag.
- The deciding count for the gate's fourth rule: public deliveries on `e` or
  `map` topics whose envelope parses, whose sender is 0 and whose plaintext
  portnum is TEXT_MESSAGE_APP, repeats not merged; with the supporting counts.
- Subscribes (each capture's first record) and reconnects inferred from gaps
  in received_at, and the merged repeats within 10 minutes after either, per
  broker and topic type; the MeshSTL rows the public broker sighted first,
  against the MeshSTL capture start.

Usage, from the repository root, Git Bash:

    uv run --frozen --with shapely==2.2.0 --with pyproj==3.8.0 python -I \
        docs/adr/0003-spike/r9_sender_zero.py OUT_DIR [PUBLIC_DIR MESHSTL_DIR NAME]
"""

from __future__ import annotations

import base64
import bisect
import json
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
from figures import az  # noqa: E402
from google.protobuf.message import DecodeError  # noqa: E402
from meshtastic.protobuf import mqtt_pb2, portnums_pb2  # noqa: E402

from ingester.decoder import StoredDelivery, decode_delivery  # noqa: E402

DATA = Path("C:/Users/Casey/mesh-terrain-data")
FRAGMENT = "capture-2026-10-28T20.jsonl"
AFTER_S = 600.0
# Public traffic runs at about 350 records a second, so 10 s with none is a lost
# connection, not a quiet spell. MeshSTL runs at about one record every 3.6 s,
# so its gaps are tried at several lengths and none is certain.
GAP_S = {"public": (10.0,), "meshstl": (120.0, 300.0, 600.0)}
# Gaps kept from each file for the profile; the thresholds above select among them.
SMALLEST_GAP_S = 10.0
TEXT = portnums_pb2.TEXT_MESSAGE_APP


@dataclass
class Scan:
    """Aggregates of one file."""

    keys: set[str] = field(default_factory=set)
    first_last: tuple[float, float] | None = None
    gaps: list[tuple[float, float]] = field(default_factory=list)
    zero: Counter[str] = field(default_factory=Counter)
    zero_text_digests: set[bytes] = field(default_factory=set)
    groups: dict[bytes, list[float]] = field(default_factory=dict)
    kinds: dict[bytes, str] = field(default_factory=dict)
    labels: dict[bytes, str] = field(default_factory=dict)


def parse(raw: bytes) -> Any:
    envelope = mqtt_pb2.ServiceEnvelope()
    try:
        envelope.ParseFromString(raw)
    except DecodeError:
        return None
    return envelope


def note_zero(scan: Scan, kind: str, packet: Any, topic: str, raw: bytes) -> None:
    """Counts of a sender-0 delivery: payload form, portnum, topic type and copy flag."""
    copy = "internet copy" if packet.via_mqtt else "radio"
    if packet.HasField("decoded"):
        port = (
            portnums_pb2.PortNum.Name(packet.decoded.portnum)
            if packet.decoded.portnum in PORTS
            else str(packet.decoded.portnum)
        )
        scan.zero[f"plaintext, portnum {port}"] += 1
        if packet.decoded.portnum == TEXT:
            scan.zero[f"text, {kind}, {copy}"] += 1
            scan.zero_text_digests.add(rules.digest(topic, raw))
    elif packet.encrypted:
        scan.zero["encrypted"] += 1
    else:
        scan.zero["no payload"] += 1
    scan.zero[f"all, {kind}, {copy}"] += 1


PORTS = set(portnums_pb2.PortNum.values())


def note_record(scan: Scan, broker: str, record: dict[str, Any], at_s: float) -> None:
    topic = record["topic"]
    kind = az.topic_kind(topic)
    if kind not in {"e", "map"}:
        return
    raw = base64.b64decode(record["payload_b64"])
    envelope = parse(raw)
    if envelope is None:
        return
    packet = envelope.packet
    if not getattr(packet, "from"):
        note_zero(scan, kind, packet, topic, raw)
        return
    if kind == "e" and packet.via_mqtt:
        return
    digest = rules.digest(topic, raw)
    scan.groups.setdefault(digest, []).append(at_s)
    scan.kinds.setdefault(digest, kind)
    if broker == "meshstl" and digest not in scan.labels:
        decoded = decode_delivery(topic, raw, datetime.fromtimestamp(at_s, UTC), THROWAWAY)
        if isinstance(decoded, StoredDelivery):
            row = decoded.row
            scan.labels[digest] = (
                row["decode_status"]
                if row["decode_status"] != "ok"
                else (
                    portnums_pb2.PortNum.Name(row["portnum"]) if row["portnum"] in PORTS else "ok"
                )
            )


# Decode statuses and portnums do not depend on the key; this one is random and unused after.
THROWAWAY = secrets.token_bytes(32)


def scan_file(task: tuple[str, str, float, float, bool]) -> Scan:
    path, broker, start_s, end_s, keys_only = task
    scan, stream = Scan(), []
    with az.open_capture(path) as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            scan.keys.update(record)
            if keys_only:
                continue
            at_s = datetime.fromisoformat(record["received_at"]).timestamp()
            if start_s <= at_s < end_s:
                stream.append(at_s)
                note_record(scan, broker, record, at_s)
    stream.sort()
    if stream:
        scan.first_last = (stream[0], stream[-1])
        scan.gaps = [
            (a, b) for a, b in zip(stream, stream[1:], strict=False) if b - a >= SMALLEST_GAP_S
        ]
    return scan


@dataclass
class Broker:
    keys: set[str] = field(default_factory=set)
    first_last: list[tuple[float, float]] = field(default_factory=list)
    gaps: list[tuple[float, float]] = field(default_factory=list)
    zero: Counter[str] = field(default_factory=Counter)
    zero_text_digests: set[bytes] = field(default_factory=set)
    groups: dict[bytes, list[float]] = field(default_factory=dict)
    kinds: dict[bytes, str] = field(default_factory=dict)
    labels: dict[bytes, str] = field(default_factory=dict)

    def absorb(self, scan: Scan) -> None:
        self.keys |= scan.keys
        if scan.first_last:
            self.first_last.append(scan.first_last)
        self.gaps += scan.gaps
        self.zero.update(scan.zero)
        self.zero_text_digests |= scan.zero_text_digests
        for digest, at in scan.groups.items():
            self.groups.setdefault(digest, []).extend(at)
        for name, values in (("kinds", scan.kinds), ("labels", scan.labels)):
            mine = getattr(self, name)
            for digest, value in values.items():
                mine.setdefault(digest, value)

    def all_gaps(self) -> list[tuple[float, float]]:
        bounds = sorted(self.first_last)
        between = [(a[1], b[0]) for a, b in zip(bounds, bounds[1:], strict=False)]
        return sorted(self.gaps + between)

    def start(self) -> float:
        return min(low for low, _ in self.first_last)


def read(broker: str, paths: list[str], window: tuple[float, float], keys_only: set[str]) -> Broker:
    merged, started = Broker(), time.monotonic()
    tasks = [(path, broker, *window, Path(path).name in keys_only) for path in paths]
    with ProcessPoolExecutor(rules.WORKERS) as pool:
        for scan in pool.map(scan_file, tasks):
            merged.absorb(scan)
    print(
        f"read {broker}: {len(paths)} files in {time.monotonic() - started:.0f} s", file=sys.stderr
    )
    return merged


def after_events(times: list[float], events: list[float]) -> int:
    count = 0
    for at_s in times:
        index = bisect.bisect_right(events, at_s) - 1
        count += index >= 0 and at_s - events[index] <= AFTER_S
    return count


def repeats_after(data: Broker, threshold_s: float) -> dict[str, Any]:
    """Merged repeats within 10 minutes after the subscribe or an inferred reconnect."""
    gaps = [gap for gap in data.all_gaps() if gap[1] - gap[0] >= threshold_s]
    events = sorted([data.start()] + [end for _, end in gaps])
    found: Counter[str] = Counter()
    total: Counter[str] = Counter()
    for digest, at in data.groups.items():
        later = sorted(at)[1:]
        total[data.kinds[digest]] += len(later)
        found[data.kinds[digest]] += after_events(later, events)
    observed = sum(1 for _ in events) * AFTER_S
    return {
        "gap threshold s": threshold_s,
        "inferred reconnects": len(gaps),
        "merged repeats, by topic type": dict(total),
        "merged repeats within 10 minutes after the subscribe or an inferred reconnect": dict(
            found
        ),
        "time inside those 10-minute windows, hours (at most)": round(observed / 3600, 2),
    }


def shared_rows(public: Broker, meshstl: Broker) -> dict[str, Any]:
    """The MeshSTL rows the public broker sighted first, against the MeshSTL capture start."""
    start = meshstl.start()
    shifted = {
        d: min(at)
        for d, at in meshstl.groups.items()
        if d in public.groups and min(public.groups[d]) < min(at)
    }
    since = sorted(at_s - start for at_s in shifted.values())
    return {
        "MeshSTL capture start (first record, UTC)": stamp(start),
        "rows the public broker sighted first": len(shifted),
        "their decode status or portnum": dict(
            Counter(meshstl.labels.get(d, "?") for d in shifted)
        ),
        "of them within 10 minutes after the MeshSTL capture start": sum(
            s <= AFTER_S for s in since
        ),
        "their MeshSTL arrival after the capture start, s: minimum, median, maximum": [
            round(since[0], 1),
            round(statistics.median(since), 1),
            round(since[-1], 1),
        ]
        if since
        else [],
        "affected link-hours (1(a)) against the capture start": "2026-10-23T22 is the capture's "
        "first clock hour (from its start) and 2026-10-23T23 its second",
    }


def stamp(at_s: float) -> str:
    return f"{datetime.fromtimestamp(at_s, UTC):%Y-%m-%dT%H:%M:%S}Z"


DECIDING = (
    "deciding count: public sender-0 plaintext TEXT_MESSAGE_APP deliveries, repeats not merged"
)


def gap_profile(data: Broker) -> dict[str, int]:
    gaps = [b - a for a, b in data.all_gaps()]
    return {f">= {t:g} s": sum(g >= t for g in gaps) for t in (10, 60, 120, 300, 600, 1800)}


def main() -> None:
    out_dir = Path(sys.argv[1])
    # The output file name; a rerun takes a new name so no earlier output is overwritten.
    target = out_dir / (sys.argv[4] if len(sys.argv) > 4 else "r9-2026-11-14.json")
    if target.exists():
        raise SystemExit(f"{target} exists; a rerun writes a new file")
    # For a smoke run on the synthetic fixtures only; the real run takes the defaults.
    public_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else DATA / "spike-capture"
    meshstl_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else DATA / "meshstl-capture"
    out_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC)
    public_paths = az.capture_paths(public_dir)
    meshstl_paths = az.capture_paths(meshstl_dir)
    public = read("public", public_paths, figures.WHOLE, {FRAGMENT})
    window = figures.window_s(rules.MESHSTL_WINDOW)
    meshstl = read("meshstl", meshstl_paths, window, set())
    zero = public.zero
    text = sum(n for key, n in zero.items() if key.startswith("text, "))
    result = {
        "started (UTC)": f"{started:%Y-%m-%dT%H:%M:%SZ}",
        "files read": {"public": len(public_paths), "meshstl": len(meshstl_paths)},
        "JSON keys of every record": {
            "public": sorted(public.keys),
            "meshstl": sorted(meshstl.keys),
        },
        DECIDING: text,
        "public sender-0 deliveries": dict(sorted(zero.items())),
        "public sender-0 text, distinct messages": len(public.zero_text_digests),
        "meshstl sender-0 deliveries": dict(sorted(meshstl.zero.items())),
        "subscribe (first record, UTC)": {
            name: stamp(data.start()) for name, data in (("public", public), ("meshstl", meshstl))
        },
        "gaps in received_at": {"public": gap_profile(public), "meshstl": gap_profile(meshstl)},
        "repeats after subscribes or inferred reconnects": {
            name: [repeats_after(data, t) for t in GAP_S[name]]
            for name, data in (("public", public), ("meshstl", meshstl))
        },
        "MeshSTL rows first sighted on the public broker": shared_rows(public, meshstl),
        "ended (UTC)": f"{datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}",
    }
    text_out = json.dumps(result, indent=1, sort_keys=True)
    target.write_text(text_out + "\n", encoding="utf-8")
    print(text_out)


if __name__ == "__main__":
    main()
