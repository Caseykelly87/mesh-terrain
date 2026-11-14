"""Gate figure (c): the spike's analysis on the archive without the fragment, with the design rules.

Casey's gate rule of 2026-11-13 compares the snapshot (d) with analyze.py's
figures on the raw files without the 2026-10-28T20 public fragment, after the
four rules by which the snapshot differs from the spike by design:

- copies: an internet copy (via_mqtt on an `e` topic) that kept its payload is
  counted, not used, so it never reaches the tally;
- repeats: deliveries with the same topic and payload bytes from one broker
  are one delivery, the first sighting in received_at order, as the snapshot
  merges them under one delivery_key;
- shared: a delivery both brokers carried is tallied at its first sighting on
  either broker, the one received_at the snapshot keeps for its row (added by
  Casey on 2026-11-13; the other broker's time is not kept);
- unparsed: a delivery on an `e` or `map` topic whose envelope does not parse,
  or whose sender (`from`) is 0, is counted as `unparsed` and not used, whether
  or not it is an internet copy, and its receipt time still counts as observed
  time (ADR 0005 item 5, as `ingester/decoder.py` applies it; added by Casey on
  2026-11-14 for the 63 sender-0 text packets read R9 found).

A first pass finds, per broker, every delivery that would be stored and how
often each (topic, payload) occurs, over the whole public capture and over the
MeshSTL window the reduction reads, so a repeat is merged even when its
sightings fall in different hourly files or on both sides of a run's window.
The second pass tallies each file with analyze.py's own tally_record, skipping
what the rules remove; everything after that (link rules, positions, figures)
is analyze.py unchanged, through figures.headline and analyze.main.

Every in-window record still counts as observed time, as in the snapshot's
coverage.json, whatever a rule does with it. The rules can be applied one at
a time with --rules, to say which one moved a figure; the result always
counts the rows both brokers carried (`shared_rows`). Hashes of
topic and payload are kept in memory only, truncated, never written or
printed. Output, under OUT_DIR/design-rules-<rules>/, is aggregates only.

Usage, from the repository root, Git Bash:

    uv run --frozen --with shapely==2.2.0 --with pyproj==3.8.0 python -I \
        docs/adr/0003-spike/reference_design_rules.py OUT_DIR \
        [--rules copies,repeats,shared,unparsed]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import figures  # noqa: E402
import reference_from_archive as reference  # noqa: E402
from figures import RUNS, az  # noqa: E402
from google.protobuf.message import DecodeError  # noqa: E402
from meshtastic.protobuf import mqtt_pb2  # noqa: E402

# The window the reduction reads from the MeshSTL capture (ingester.reduce.MESHSTL_WINDOW).
MESHSTL_WINDOW = ("2026-10-23T22:09:00+00:00", "2026-10-24T22:09:00+00:00")
WORKERS = max(1, (os.cpu_count() or 2) - 1)


def classify(kind: str, raw: bytes) -> str:
    """stored, copy_with_payload, copy or dropped, as ingester.decoder would land it."""
    if kind not in {"e", "map"}:
        return "dropped"
    envelope = mqtt_pb2.ServiceEnvelope()
    try:
        envelope.ParseFromString(raw)
    except DecodeError:
        return "dropped"
    packet = envelope.packet
    if not getattr(packet, "from"):
        return "dropped"
    if kind == "e" and packet.via_mqtt:
        has_payload = packet.HasField("decoded") or bool(packet.encrypted)
        return "copy_with_payload" if has_payload else "copy"
    return "stored"


def digest(topic: str, raw: bytes) -> bytes:
    data = topic.encode("utf-8")
    return hashlib.sha256(len(data).to_bytes(4, "big") + data + raw).digest()[:16]


def records(path: str, start_s: float, end_s: float) -> Any:
    """(at_s, kind, topic, record, line length) of each readable record in [start_s, end_s)."""
    with az.open_capture(path) as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            at_s = datetime.fromisoformat(record["received_at"]).timestamp()
            if start_s <= at_s < end_s and not record.get("thin"):
                topic = record["topic"]
                yield at_s, az.topic_kind(topic), topic, record, len(line)


def first_pass(task: tuple[str, float, float]) -> tuple[dict[bytes, list[float]], int]:
    """Per stored (topic, payload): [sightings, first at_s]; and the copies that kept a payload."""
    path, start_s, end_s = task
    seen: dict[bytes, list[float]] = {}
    copies = 0
    for at_s, kind, topic, record, _ in records(path, start_s, end_s):
        raw = base64.b64decode(record["payload_b64"])
        outcome = classify(kind, raw)
        copies += outcome == "copy_with_payload"
        if outcome == "stored":
            entry = seen.setdefault(digest(topic, raw), [0, at_s])
            entry[0] += 1
            entry[1] = min(entry[1], at_s)
    return seen, copies


Firsts = dict[bytes, tuple[float, str]]


def note_minute(tally: Any, at_s: float) -> None:
    """Observed time, as analyze.add_volume and the snapshot's coverage.json record it."""
    minute = int(at_s // 60)
    low, high = tally.minute_bounds.get(minute, (at_s, at_s))
    tally.minute_bounds[minute] = (min(low, at_s), max(high, at_s))


def kept_time(
    task: tuple[str, Firsts, dict[bytes, float], frozenset[str]],
    key: bytes,
    at_s: float,
    kept: set[bytes],
) -> float | None:
    """The time a stored delivery is tallied at, or None when the repeats rule merges it."""
    path, repeated, shift, rules = task
    if "repeats" in rules and key in repeated:
        if key in kept or repeated[key] != (at_s, path):
            return None
        kept.add(key)
    return shift.get(key, at_s) if "shared" in rules else at_s


def second_pass(
    task: tuple[str, float, float, Any, Firsts, dict[bytes, float], frozenset[str]],
) -> Any:
    """analyze.tally_file for one file, without what the rules remove.

    Every in-window record still counts as observed time, as in the snapshot's
    coverage, whatever the rules do with it.
    """
    path = task[0]
    return tally_items(records(path, -math.inf, math.inf), task)


def tally_items(
    items: Any, task: tuple[str, float, float, Any, Firsts, dict[bytes, float], frozenset[str]]
) -> Any:
    """second_pass over (at_s, kind, topic, record, line length) items of one file."""
    tally, kept = az.Tally(), set()
    for item in items:
        tally_item(tally, kept, item, task)
    return finish_tally(tally)


def finish_tally(tally: Any) -> Any:
    """Plain dicts in place of defaultdicts, so the tally pickles, as analyze.tally_file does."""
    tally.heard, tally.boxes = dict(tally.heard), dict(tally.boxes)
    tally.regional_sets = dict(tally.regional_sets)
    return tally


def tally_item(
    tally: Any,
    kept: set[bytes],
    item: tuple[float, str, str, dict[str, Any], int],
    task: tuple[str, float, float, Any, Firsts, dict[bytes, float], frozenset[str]],
) -> None:
    """One record into the tally, with the rules applied (the body of analyze.tally_file)."""
    path, start_s, end_s, roots, repeated, shift, rules = task
    actual_s, kind, topic, record, size = item
    if roots is not None and az.topic_root(topic) not in roots:
        return
    in_window = start_s <= actual_s < end_s
    raw = base64.b64decode(record["payload_b64"])
    outcome = classify(kind, raw)
    at_s: float | None = actual_s
    if outcome == "copy_with_payload" and "copies" in rules:
        at_s = None
    elif outcome == "dropped" and kind in {"e", "map"} and "unparsed" in rules:
        at_s = None
    elif outcome == "stored":
        at_s = kept_time((path, repeated, shift, rules), digest(topic, raw), actual_s, kept)
    parsed = None
    if at_s is not None and start_s <= at_s < end_s:
        tally.counts[("kind", kind)] += 1
        if kind in {"e", "map"}:
            parsed = az.tally_record(tally, at_s, kind, record)
    if in_window and (at_s is not None or parsed is not None):
        tally.add_volume(actual_s, topic, kind, parsed, size)
    elif in_window:
        note_minute(tally, actual_s)


class Rules:
    """The first pass's results per broker and a loader that applies the rules."""

    def __init__(self, dirs: dict[str, Path], rules: frozenset[str]) -> None:
        self.dirs, self.rules = dirs, rules
        self.repeated: dict[str, Firsts] = {}
        self.firsts: dict[str, dict[bytes, float]] = {}
        self.counts: dict[str, dict[str, int]] = {}
        windows = {"public": figures.WHOLE, "meshstl": figures.window_s(MESHSTL_WINDOW)}
        for broker, window in windows.items():
            self._first_pass(broker, window)
        # The "shared" rule: a row both brokers delivered is tallied at its first sighting on
        # either broker, as the snapshot's received_at holds it.
        self.shift: dict[str, dict[bytes, float]] = {}
        for broker, other in (("public", "meshstl"), ("meshstl", "public")):
            own, theirs = self.firsts[broker], self.firsts[other]
            shared = own.keys() & theirs.keys()
            self.shift[broker] = {k: theirs[k] for k in shared if theirs[k] < own[k]}

    def _first_pass(self, broker: str, window: tuple[float, float]) -> None:
        started = time.monotonic()
        # key -> [sightings, first at_s, file of the first sighting]; files are in hour order,
        # so on a tie in at_s the earlier file holds the first sighting.
        merged: dict[bytes, list[Any]] = {}
        copies = 0
        paths = az.capture_paths(self.dirs[broker])
        with ProcessPoolExecutor(WORKERS) as pool:
            tasks = [(path, *window) for path in paths]
            for path, (seen, file_copies) in zip(paths, pool.map(first_pass, tasks), strict=True):
                copies += file_copies
                for key, (count, at_s) in seen.items():
                    entry = merged.setdefault(key, [0, at_s, path])
                    entry[0] += count
                    if at_s < entry[1]:
                        entry[1], entry[2] = at_s, path
        self.repeated[broker] = {k: (at_s, p) for k, (n, at_s, p) in merged.items() if n > 1}
        self.firsts[broker] = {k: at_s for k, (_, at_s, _) in merged.items()}
        self.counts[broker] = {
            "stored deliveries": int(sum(entry[0] for entry in merged.values())),
            "repeats merged": int(sum(entry[0] - 1 for entry in merged.values())),
            "copies with a payload, counted not used": copies,
        }
        print(f"first pass {broker} in {time.monotonic() - started:.0f} s", file=sys.stderr)

    def load_capture(self, data_dir: Path, start_s: float, end_s: float, roots: Any = None) -> Any:
        broker = next(
            b for b, path in self.dirs.items() if path.resolve() == Path(data_dir).resolve()
        )
        tasks = [
            (path, start_s, end_s, roots, self.repeated[broker], self.shift[broker], self.rules)
            for path in az.capture_paths(data_dir)
        ]
        total = az.Tally()
        with ProcessPoolExecutor(WORKERS) as pool:
            for part in pool.map(second_pass, tasks):
                total.merge(part)
        return total

    def shared_rows(self) -> dict[str, int]:
        """Rows both brokers delivered, by which broker sighted them first."""
        public, meshstl = self.firsts["public"], self.firsts["meshstl"]
        shared = public.keys() & meshstl.keys()
        return {
            "both brokers": len(shared),
            "public first": sum(1 for key in shared if public[key] < meshstl[key]),
            "meshstl first": sum(1 for key in shared if meshstl[key] < public[key]),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--rules", default="copies,repeats,shared,unparsed")
    parser.add_argument("--public", type=Path, default=reference.DATA / "spike-capture")
    parser.add_argument("--meshstl", type=Path, default=reference.DATA / "meshstl-capture")
    args = parser.parse_args()
    rules = frozenset(args.rules.split(","))
    name = "design-rules-" + "-".join(sorted(rules))
    out_dir = args.out_dir / name
    out_dir.mkdir(parents=True, exist_ok=True)
    dirs = {"public": args.public, "meshstl": args.meshstl}
    archive = reference.Archive(dirs, without_fragment=True)
    applied = Rules(dirs, rules)
    archive.load_capture = applied.load_capture
    runs: dict[str, Any] = {}
    for run in RUNS:
        text = reference.run_main(archive, run)
        (out_dir / f"{run}.txt").write_text(text, encoding="utf-8")
        found = figures.headline(archive.load, run)
        printed = figures.printed_headline(text)
        runs[run] = {
            "figures": found,
            "differs_from_printed_table": figures.matches_printed(found, printed),
        }
    whole = {
        "public": archive.load("public", *figures.WHOLE, None),
        "meshstl": archive.load("meshstl", *figures.window_s(figures.SHARED), None),
    }
    result = {
        "variant": name,
        "files_read": archive.files_read,
        "runs": runs,
        "counts": {broker: reference.counts(tally) for broker, tally in whole.items()},
        "rules": applied.counts,
        "shared_rows": applied.shared_rows(),
    }
    path = args.out_dir / f"reference-{name}.json"
    path.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
