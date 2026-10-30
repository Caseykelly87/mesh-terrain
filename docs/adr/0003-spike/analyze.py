"""Decode and measure a spike capture for ADR 0003.

Reads every capture-*.jsonl and capture-*.jsonl.gz file in a directory, one
worker process per file, parses each payload as a ServiceEnvelope, decrypts
default-key packets, decodes Position, NodeInfo, Telemetry, NeighborInfo and
MapReport by portnum, and prints the measurements the Phase 1 spike asks for:
(a) relay_node split by via_mqtt, (b) NeighborInfo, (c) precision_bits,
regional topics, area counts, (e) the link-hour yield per link rule with a
funnel that names why receipts fail, a position-free diagnostic, and the (f)
widening check. Every rate divides by the hours actually covered, which are
the capture's segments (runs of records with no gap over 15 minutes), not the
wall clock from the first record to the last. Weekly figures weight the
weekday and weekend hourly rates by 120 and 48 hours. Output is aggregate
only: no node id, node name or coordinate is printed. It is not the
production decode asset and writes nothing to disk.

Usage: python analyze.py DATA_DIR [MSA_CSV [STATES_CSV]] [--start ISO] [--end ISO]
    [--roots MO,IL,MeshSTL] [--positions-from OTHER_DATA_DIR]
"""

from __future__ import annotations

import argparse
import base64
import bisect
import csv
import gzip
import json
import math
import os
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Any

import shapely
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from google.protobuf.message import DecodeError
from meshtastic.protobuf import mesh_pb2, mqtt_pb2, portnums_pb2, telemetry_pb2
from pyproj import Geod
from shapely.prepared import prep

DEFAULT_KEY = base64.b64decode("1PG7OiApB1nwvP+rz05pAQ==")
ST_LOUIS_LATITUDE_DEG = 38.63
USABLE_BITS = 15
COARSE_BITS = 12
POSITION_WINDOW_S = 30 * 60
MIN_PACKETS_PER_HOUR = 3
MIN_DISTANCE_M = 500.0
MAX_DISTANCE_M = 60_000.0
VALIDATION_DISTANCE_M = 3000.0
RSSI_RANGE_DBM = (-140, 0)
SNR_RANGE_DB = (-25.0, 15.0)
WIDEN_THRESHOLD_PER_WEEK = 300
HOURS_PER_WEEK = 168.0
REGIONAL_SEGMENTS = ("MeshSTL", "MO", "IL")
# Positions kept in memory: Missouri plus Illinois with a margin, as
# (lon_min, lat_min, lon_max, lat_max) in EPSG:4326 degrees.
KEEP_BBOX_DEG = (-96.0, 35.8, -86.9, 42.7)
RULES = ("relay_node", "neighbor_info", "combined", "relay_node_strict")
PRECISE_BITS = 20
GROWTH_HOURS = (4, 8, 12, 24, 36, 48, 60)
FIRST_POSITION_BINS_H = (1, 2, 4, 8, 12, 24, 48, 72, 168)
# A gap longer than this between records starts a new segment, and the gap
# is not counted as observed time.
SEGMENT_GAP_S = 15 * 60
# St. Louis is on CDT for the whole capture (DST ends 2026-11-01); weekday and
# weekend are local days.
LOCAL_TZ = timezone(timedelta(hours=-5), "CDT")
WEEKDAY_HOURS_PER_WEEK = 120.0
WEEKEND_HOURS_PER_WEEK = 48.0
ROOT_CLASSES = ("msh/US/MO", "msh/US/IL", "msh/US/MeshSTL", "other msh/US")
THIN_FIELDS = (
    "gateway",
    "sender",
    "packet_id",
    "relay",
    "hop_limit",
    "hop_start",
    "via_mqtt",
    "rx_snr",
    "rx_rssi",
    "portnum",
)
GEOD = Geod(ellps="WGS84")
csv.field_size_limit(sys.maxsize)

KNOWN_PORTS = {
    portnums_pb2.POSITION_APP,
    portnums_pb2.NEIGHBORINFO_APP,
    portnums_pb2.NODEINFO_APP,
    portnums_pb2.TELEMETRY_APP,
    portnums_pb2.MAP_REPORT_APP,
    portnums_pb2.TEXT_MESSAGE_APP,
    portnums_pb2.ROUTING_APP,
    portnums_pb2.TRACEROUTE_APP,
    portnums_pb2.STORE_FORWARD_APP,
    portnums_pb2.RANGE_TEST_APP,
}


@dataclass
class Parsed:
    """One decoded delivery; coordinates are 1e-7 degree integers as sent."""

    at_s: float
    kind: str
    region: str
    status: str
    reason: str
    gateway: int = 0
    sender: int = 0
    packet_id: int = 0
    relay: int = 0
    hop_limit: int = 0
    hop_start: int = 0
    via_mqtt: bool = False
    rx_snr: float = 0.0
    rx_rssi: int = 0
    ok_to_mqtt: bool | None = None
    portnum: int | None = None
    position: tuple[int, int, int] | None = None
    map_report: tuple[int, int, int] | None = None
    neighbors: tuple[tuple[int, float], ...] = ()
    root: str = ""


def topic_root(topic: str) -> str:
    """The segment after msh/US: a state or community name, or the protocol version."""
    parts = topic.split("/")
    return parts[2] if len(parts) > 2 else ""


def root_class(root: str) -> str:
    """One of ROOT_CLASSES for a topic root segment."""
    return f"msh/US/{root}" if root in REGIONAL_SEGMENTS else "other msh/US"


def copy_class(kind: str, parsed: Parsed | None) -> str:
    """Storage class of one record: radio delivery, internet copy, or another topic kind."""
    if kind != "e":
        return kind
    if parsed is None or parsed.status == "malformed" or not parsed.sender:
        return "e, unparsed or no sender"
    return "e, internet copy (via_mqtt)" if parsed.via_mqtt else "e, radio"


def topic_kind(topic: str) -> str:
    """Classify a topic by its protocol segment: e (protobuf), json, map, stat, other."""
    for part in topic.split("/")[2:]:
        if part in {"e", "c", "json", "map", "stat"}:
            return "e" if part == "c" else part
    return "other"


def topic_region(topic: str) -> str:
    """The segment after msh/US when it is one we treat as St. Louis, else empty."""
    parts = topic.split("/")
    return parts[2] if len(parts) > 2 and parts[2] in REGIONAL_SEGMENTS else ""


def node_id_from_gateway(gateway: str) -> int:
    """Parse a "!xxxxxxxx" gateway id to its node number; 0 when it is not one."""
    if gateway.startswith("!") and len(gateway) == 9:
        try:
            return int(gateway[1:], 16)
        except ValueError:
            return 0
    return 0


def nonce(packet_id: int, sender: int) -> bytes:
    return packet_id.to_bytes(8, "little") + sender.to_bytes(4, "little") + b"\x00" * 4


def decrypt_default(ciphertext: bytes, packet_id: int, sender: int) -> bytes:
    cipher = Cipher(algorithms.AES(DEFAULT_KEY), modes.CTR(nonce(packet_id, sender)))
    decryptor = cipher.decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


def payload_data(mesh_packet: mesh_pb2.MeshPacket) -> tuple[mesh_pb2.Data | None, str]:
    """Return the Data message and a reason string; the message is None on failure.

    The public broker forwards many default-key packets with the header intact
    and the encrypted payload removed; the caller records that as the
    contract status "stripped" (ing_03), not as a wrong key.
    """
    if mesh_packet.HasField("decoded"):
        return mesh_packet.decoded, "plaintext"
    if mesh_packet.pki_encrypted:
        return None, "pki"
    if not mesh_packet.encrypted:
        return None, "stripped"
    sender = getattr(mesh_packet, "from")
    if not mesh_packet.id or not sender:
        return None, "zero_id_or_from"
    data = mesh_pb2.Data()
    try:
        data.ParseFromString(decrypt_default(mesh_packet.encrypted, mesh_packet.id, sender))
    except DecodeError:
        return None, "wrong_key"
    # A wrong key yields noise that usually fails to parse; when it parses, the
    # portnum is almost never one a real node sends.
    if data.portnum == 0 or data.portnum > portnums_pb2.MAX:
        return None, "wrong_key"
    return data, "default_key"


def decode_data(parsed: Parsed, data: mesh_pb2.Data) -> None:
    """Fill the portnum-specific fields of parsed from a Data message."""
    parsed.portnum = data.portnum
    if data.portnum == portnums_pb2.POSITION_APP:
        position = mesh_pb2.Position()
        position.ParseFromString(data.payload)
        if position.latitude_i or position.longitude_i:
            parsed.position = (position.latitude_i, position.longitude_i, position.precision_bits)
    elif data.portnum == portnums_pb2.NEIGHBORINFO_APP:
        info = mesh_pb2.NeighborInfo()
        info.ParseFromString(data.payload)
        parsed.neighbors = tuple((n.node_id, n.snr) for n in info.neighbors)
    elif data.portnum == portnums_pb2.NODEINFO_APP:
        mesh_pb2.User().ParseFromString(data.payload)
    elif data.portnum == portnums_pb2.TELEMETRY_APP:
        telemetry_pb2.Telemetry().ParseFromString(data.payload)
    elif data.portnum == portnums_pb2.MAP_REPORT_APP:
        report = mqtt_pb2.MapReport()
        report.ParseFromString(data.payload)
        if report.latitude_i or report.longitude_i:
            parsed.map_report = (report.latitude_i, report.longitude_i, report.position_precision)
    if data.portnum not in KNOWN_PORTS:
        parsed.status = "unknown_port"


def parse_envelope(at_s: float, kind: str, region: str, raw: bytes) -> Parsed:
    envelope = mqtt_pb2.ServiceEnvelope()
    try:
        envelope.ParseFromString(raw)
    except DecodeError:
        return Parsed(at_s, kind, region, "malformed", "envelope")
    packet = envelope.packet
    parsed = Parsed(
        at_s,
        kind,
        region,
        "ok",
        "",
        gateway=node_id_from_gateway(envelope.gateway_id),
        sender=getattr(packet, "from"),
        packet_id=packet.id,
        relay=packet.relay_node,
        hop_limit=packet.hop_limit,
        hop_start=packet.hop_start,
        via_mqtt=packet.via_mqtt,
        rx_snr=packet.rx_snr,
        rx_rssi=packet.rx_rssi,
    )
    data, parsed.reason = payload_data(packet)
    if data is None:
        parsed.status = "stripped" if parsed.reason == "stripped" else "decrypt_failed"
        return parsed
    parsed.ok_to_mqtt = bool(data.bitfield & 1)
    try:
        decode_data(parsed, data)
    except DecodeError:
        parsed.status, parsed.reason = "malformed", "payload"
    return parsed


def in_keep_bbox(lat_i: int, lon_i: int) -> bool:
    lon, lat = lon_i / 1e7, lat_i / 1e7
    return (
        KEEP_BBOX_DEG[0] <= lon <= KEEP_BBOX_DEG[2] and KEEP_BBOX_DEG[1] <= lat <= KEEP_BBOX_DEG[3]
    )


def signal_in_range(parsed: Parsed) -> bool:
    """lnk_06: rx_rssi set and in [-140, 0] dBm, rx_snr in [-25, 15] dB."""
    rssi_ok = parsed.rx_rssi != 0 and RSSI_RANGE_DBM[0] <= parsed.rx_rssi <= RSSI_RANGE_DBM[1]
    return rssi_ok and SNR_RANGE_DB[0] <= parsed.rx_snr <= SNR_RANGE_DB[1]


@dataclass
class Tally:
    """Aggregates from one or more capture files; small enough to pickle between processes.

    `heard` and `receipts` hold radio receipts only (via_mqtt false): a packet
    the gateway received over MQTT was not a radio hop at that gateway.
    Receipts carry the topic root they arrived under, for the subscription
    scope question.
    """

    first_s: float = math.inf
    last_s: float = 0.0
    counts: Counter[tuple[Any, ...]] = field(default_factory=Counter)
    sender_kind: dict[int, int] = field(default_factory=dict)
    heard: dict[int, set[int]] = field(default_factory=lambda: defaultdict(set))
    receipts: dict[tuple[int, int, int], tuple[float, int, bool, str]] = field(default_factory=dict)
    minute_bounds: dict[int, tuple[float, float]] = field(default_factory=dict)
    neighbor_reports: list[tuple[float, int, tuple[tuple[int, float], ...]]] = field(
        default_factory=list
    )
    positions: list[tuple[int, float, int, int, int, str]] = field(default_factory=list)
    positioned_senders: set[int] = field(default_factory=set)
    payload_senders: set[int] = field(default_factory=set)
    precise_senders: set[int] = field(default_factory=set)
    radio_keys: dict[tuple[int, int, int], str] = field(default_factory=dict)
    first_heard: dict[int, float] = field(default_factory=dict)
    first_position: dict[int, float] = field(default_factory=dict)
    first_usable: dict[int, float] = field(default_factory=dict)
    boxes: dict[int, set[tuple[int, int, int]]] = field(default_factory=lambda: defaultdict(set))
    regional_sets: dict[tuple[str, str], set[int]] = field(default_factory=lambda: defaultdict(set))

    def add(self, parsed: Parsed) -> None:
        self.first_s = min(self.first_s, parsed.at_s)
        self.last_s = max(self.last_s, parsed.at_s)
        self.counts[("status", parsed.status)] += 1
        self.counts[("reason", f"{parsed.status}: {parsed.reason or 'envelope'}")] += 1
        if parsed.ok_to_mqtt is not None:
            self.counts[("consent", str(parsed.ok_to_mqtt))] += 1
        if parsed.portnum is not None:
            self.counts[("port", str(parsed.portnum))] += 1
        if parsed.kind == "e" and parsed.status != "malformed" and parsed.sender:
            self.add_header(parsed)
            if parsed.region:
                self.add_regional(parsed)
        self.add_payload(parsed)

    def add_header(self, parsed: Parsed) -> None:
        mode = "mqtt" if parsed.via_mqtt else "rf"
        stripped = parsed.reason == "stripped"
        relayed = bool(parsed.relay)
        flags = {
            "envelopes": True,
            "own": parsed.gateway == parsed.sender,
            "stripped": stripped,
            "relay_set": relayed,
            "relay_set_stripped": relayed and stripped,
            "self_relay": relayed and parsed.relay == parsed.sender & 0xFF,
            "hop_limit_zero": parsed.hop_limit == 0,
            "hop_start_set": bool(parsed.hop_start),
        }
        # Radio envelopes of the gateway's own packets are not receipts, so (a)
        # also reports radio envelopes without them.
        modes = [mode] if parsed.via_mqtt or flags["own"] else [mode, "rf_not_own"]
        for name, flag in flags.items():
            for each in modes:
                self.counts[("mode", each, name)] += int(flag)
        self.counts[("mode_reason", mode, f"{parsed.status}: {parsed.reason}")] += 1
        bit = 1 if stripped else 2
        self.sender_kind[parsed.sender] = self.sender_kind.get(parsed.sender, 0) | bit
        if parsed.gateway and not parsed.via_mqtt:
            self.add_radio_receipt(parsed)

    def add_radio_receipt(self, parsed: Parsed) -> None:
        self.heard[parsed.gateway].add(parsed.sender)
        first_at(self.first_heard, parsed.sender, parsed.at_s)
        if parsed.gateway == parsed.sender:
            return
        key = (parsed.gateway, parsed.sender, parsed.packet_id)
        self.radio_keys.setdefault(key, parsed.root)
        if not parsed.relay:
            return
        if key not in self.receipts or parsed.at_s < self.receipts[key][0]:
            self.receipts[key] = (parsed.at_s, parsed.relay, signal_in_range(parsed), parsed.root)

    def add_volume(
        self, at_s: float, topic: str, kind: str, parsed: Parsed | None, size: int
    ) -> None:
        """Minute coverage, hourly counts and JSON lines bytes by topic root and copy class."""
        minute = int(at_s // 60)
        low, high = self.minute_bounds.get(minute, (at_s, at_s))
        self.minute_bounds[minute] = (min(low, at_s), max(high, at_s))
        hour = minute // 60
        copy = copy_class(kind, parsed)
        self.counts[("hour", hour, "records")] += 1
        self.counts[("hour", hour, "bytes")] += size
        self.counts[("hour", hour, copy)] += 1
        root = root_class(topic_root(topic))
        self.counts[("volume_bytes", root, copy)] += size
        self.counts[("volume_records", root, copy)] += 1

    def add_payload(self, parsed: Parsed) -> None:
        if parsed.status == "ok" and parsed.sender:
            self.payload_senders.add(parsed.sender)
        if parsed.portnum == portnums_pb2.NEIGHBORINFO_APP:
            self.neighbor_reports.append((parsed.at_s, parsed.sender, parsed.neighbors))
        for source, value in (("position", parsed.position), ("map", parsed.map_report)):
            if value is None or not parsed.sender:
                continue
            self.positioned_senders.add(parsed.sender)
            self.counts[(f"{source}_bits", str(value[2]))] += 1
            self.boxes[parsed.sender].add(value)
            first_at(self.first_position, parsed.sender, parsed.at_s)
            if value[2] >= USABLE_BITS:
                first_at(self.first_usable, parsed.sender, parsed.at_s)
            if value[2] >= PRECISE_BITS:
                self.precise_senders.add(parsed.sender)
            if in_keep_bbox(value[0], value[1]):
                row = (parsed.sender, parsed.at_s, value[0], value[1], value[2], source)
                self.positions.append(row)

    def add_regional(self, parsed: Parsed) -> None:
        segment = parsed.region
        relayed = bool(parsed.relay) and parsed.gateway != parsed.sender
        self.counts[("regional", segment, "packets")] += 1
        self.counts[("regional", segment, "rf")] += int(not parsed.via_mqtt)
        self.counts[("regional", segment, "relayed")] += int(relayed)
        self.counts[("regional", segment, "payload")] += int(parsed.status == "ok")
        self.counts[("regional", segment, "positions")] += int(parsed.position is not None)
        self.regional_sets[(segment, "gateways")].add(parsed.gateway)
        if parsed.sender:
            self.regional_sets[(segment, "senders")].add(parsed.sender)

    def merge(self, other: Tally) -> None:
        self.first_s = min(self.first_s, other.first_s)
        self.last_s = max(self.last_s, other.last_s)
        self.counts.update(other.counts)
        for sender, bits in other.sender_kind.items():
            self.sender_kind[sender] = self.sender_kind.get(sender, 0) | bits
        for gateway, senders in other.heard.items():
            self.heard[gateway] |= senders
        for key, value in other.receipts.items():
            if key not in self.receipts or value[0] < self.receipts[key][0]:
                self.receipts[key] = value
        self.neighbor_reports.extend(other.neighbor_reports)
        self.positions.extend(other.positions)
        self.positioned_senders |= other.positioned_senders
        self.payload_senders |= other.payload_senders
        self.precise_senders |= other.precise_senders
        for key, root in other.radio_keys.items():
            self.radio_keys.setdefault(key, root)
        for minute, (low, high) in other.minute_bounds.items():
            mine = self.minute_bounds.get(minute, (low, high))
            self.minute_bounds[minute] = (min(mine[0], low), max(mine[1], high))
        for mine, theirs in (
            (self.first_heard, other.first_heard),
            (self.first_position, other.first_position),
            (self.first_usable, other.first_usable),
        ):
            for node, at_s in theirs.items():
                first_at(mine, node, at_s)
        for node, boxes in other.boxes.items():
            self.boxes[node] |= boxes
        for key, values in other.regional_sets.items():
            self.regional_sets[key] |= values

    def coverage(self) -> Coverage:
        return Coverage.from_minutes(self.minute_bounds)

    def hours(self) -> float:
        """Observed hours: the sum of segment spans, never the first-to-last wall clock."""
        return max(self.coverage().hours(), 1e-9)


@dataclass
class Rates:
    """One measure as observed count, hourly rate and weekly extrapolation."""

    observed: int
    hours: float
    per_hour: float
    weekday_per_hour: float
    weekend_per_hour: float
    week_flat: float
    week_weighted: float


@dataclass
class Coverage:
    """The time a capture actually observed, as (start_s, end_s) segments."""

    segments: list[tuple[float, float]]

    @classmethod
    def from_minutes(cls, bounds: dict[int, tuple[float, float]]) -> Coverage:
        segments: list[tuple[float, float]] = []
        for minute in sorted(bounds):
            low, high = bounds[minute]
            if segments and low - segments[-1][1] <= SEGMENT_GAP_S:
                segments[-1] = (segments[-1][0], high)
            else:
                segments.append((low, high))
        return cls(segments)

    def hours(self) -> float:
        return sum(end - start for start, end in self.segments) / 3600.0

    def overlap_h(self, low_s: float, high_s: float) -> float:
        """Observed hours between two instants."""
        total = sum(max(0.0, min(end, high_s) - max(start, low_s)) for start, end in self.segments)
        return total / 3600.0

    def hour_h(self, hour: int) -> float:
        return self.overlap_h(hour * 3600.0, (hour + 1) * 3600.0)

    def clock_hours(self) -> list[int]:
        hours: set[int] = set()
        for start, end in self.segments:
            hours.update(range(int(start // 3600), int(end // 3600) + 1))
        return sorted(hours)

    def segment_of(self, hour: int) -> int:
        """1-based segment number of a clock hour; 0 when the hour is not observed."""
        for index, (start, end) in enumerate(self.segments, 1):
            if start < (hour + 1) * 3600 and end >= hour * 3600:
                return index
        return 0

    def class_hours(self) -> dict[str, float]:
        totals = {"weekday": 0.0, "weekend": 0.0}
        for hour in self.clock_hours():
            totals[day_class(hour)] += self.hour_h(hour)
        return totals

    def rates(self, hours_of_events: Iterable[int]) -> Rates:
        """Rates for events given by the clock hour each falls in."""
        by_class: Counter[str] = Counter(day_class(hour) for hour in hours_of_events)
        observed = sum(by_class.values())
        exposure = self.class_hours()
        per_hour = observed / max(self.hours(), 1e-9)
        # A class with no observed hours takes the overall rate.
        weekday = by_class["weekday"] / exposure["weekday"] if exposure["weekday"] else per_hour
        weekend = by_class["weekend"] / exposure["weekend"] if exposure["weekend"] else per_hour
        weighted = weekday * WEEKDAY_HOURS_PER_WEEK + weekend * WEEKEND_HOURS_PER_WEEK
        return Rates(
            observed, self.hours(), per_hour, weekday, weekend, per_hour * HOURS_PER_WEEK, weighted
        )


def local_time(hour: int) -> datetime:
    return datetime.fromtimestamp(hour * 3600, LOCAL_TZ)


def day_class(hour: int) -> str:
    """weekday or weekend for the local day the clock hour starts in."""
    return "weekend" if local_time(hour).weekday() >= 5 else "weekday"


def first_at(table: dict[int, float], node: int, at_s: float) -> None:
    if node not in table or at_s < table[node]:
        table[node] = at_s


def thin_parsed(at_s: float, record: dict[str, Any]) -> Parsed:
    """A Parsed from a headers-only capture record (CAPTURE_THIN=1); no payload fields."""
    fields = {name: record[name] for name in THIN_FIELDS if name in record}
    region = topic_region(record["topic"])
    return Parsed(at_s, record["kind"], region, record["status"], record["reason"], **fields)


def open_capture(path: str) -> Any:
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="ascii")
    return open(path, encoding="ascii")  # noqa: SIM115 (closed by the caller's with)


def tally_file(
    path: str,
    start_s: float = -math.inf,
    end_s: float = math.inf,
    roots: frozenset[str] | None = None,
) -> Tally:
    """Tally one hourly capture file; a partly written last line is counted and skipped.

    With roots set, records whose topic root is not in it are skipped, which
    is what a subscription to only those topic prefixes would have received.
    """
    tally = Tally()
    with open_capture(path) as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                tally.counts[("kind", "unreadable_line")] += 1
                continue
            at_s = datetime.fromisoformat(record["received_at"]).timestamp()
            topic = record["topic"]
            if not start_s <= at_s < end_s or (
                roots is not None and topic_root(topic) not in roots
            ):
                continue
            kind = record["kind"] if record.get("thin") else topic_kind(topic)
            tally.counts[("kind", kind)] += 1
            parsed = None
            if kind in {"e", "map"}:
                parsed = tally_record(tally, at_s, kind, record)
            tally.add_volume(at_s, topic, kind, parsed, len(line))
    tally.heard = dict(tally.heard)
    tally.boxes = dict(tally.boxes)
    tally.regional_sets = dict(tally.regional_sets)
    return tally


def tally_record(tally: Tally, at_s: float, kind: str, record: dict[str, Any]) -> Parsed:
    topic = record["topic"]
    if record.get("thin"):
        tally.counts[("kind", "thin")] += 1
        parsed = thin_parsed(at_s, record)
    else:
        raw = base64.b64decode(record["payload_b64"])
        parsed = parse_envelope(at_s, kind, topic_region(topic), raw)
    parsed.root = topic_root(topic)
    tally.add(parsed)
    if kind == "e" and parsed.via_mqtt:
        # The count-only store for internet copies keys on (hour, topic, gateway);
        # the topic already ends in the gateway id.
        tally.counts[("internet_key", int(at_s // 3600), topic)] = 1
    return parsed


def capture_paths(data_dir: Path) -> list[str]:
    """Hourly files, plain or gzipped; the same hour in both forms is an error."""
    paths = sorted(data_dir.glob("capture-*.jsonl")) + sorted(data_dir.glob("capture-*.jsonl.gz"))
    hours = Counter(p.name.removesuffix(".gz") for p in paths)
    doubled = sorted(name for name, n in hours.items() if n > 1)
    if doubled:
        raise ValueError(f"hour present both plain and gzipped: {doubled}")
    return sorted(str(p) for p in paths)


def load_capture(
    data_dir: Path, start_s: float, end_s: float, roots: frozenset[str] | None = None
) -> Tally:
    paths = capture_paths(data_dir)
    total = Tally()
    workers = max(1, min(len(paths), (os.cpu_count() or 2) - 1))
    work = partial(tally_file, start_s=start_s, end_s=end_s, roots=roots)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for part in pool.map(work, paths):
            total.merge(part)
    return total


def box_half_width_m(precision_bits: int, latitude_deg: float) -> tuple[float, float]:
    """Half-width of the position box in metres, north-south and east-west.

    A position at N bits keeps the top N bits of the 32-bit integer degrees
    (1e-7 degree units), so the box spans 2 ** (32 - N) units; half of that
    is 2 ** (31 - N) units, or 2 ** (31 - N) * 1e-7 degrees.
    """
    half_deg = 2 ** (31 - precision_bits) * 1e-7
    metres_per_deg_lat = 111_320.0
    north_south_m = half_deg * metres_per_deg_lat
    return north_south_m, north_south_m * math.cos(math.radians(latitude_deg))


def load_area(csv_path: Path, wanted: set[str] | None) -> shapely.Geometry:
    """Union of the polygons in a boundary CSV (geoid, geometry_wkt, optional part).

    Rows with the same geoid are WKT chunks joined in part order, which is how
    the dbt seed stores a polygon longer than a CSV field may be.
    """
    chunks: dict[str, list[tuple[int, str]]] = defaultdict(list)
    with csv_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if wanted is None or row["geoid"] in wanted:
                chunks[row["geoid"]].append((int(row.get("part") or 1), row["geometry_wkt"]))
    if not chunks:
        raise ValueError(f"no matching rows in {csv_path}")
    geometries = [
        shapely.from_wkt("".join(text for _, text in sorted(parts))) for parts in chunks.values()
    ]
    return shapely.union_all(geometries)


class Area:
    """A boundary in EPSG:4326 with a cached point-in-polygon test on integer coordinates."""

    def __init__(self, name: str, geometry: shapely.Geometry) -> None:
        self.name = name
        self.prepared = prep(geometry)
        self.bounds = geometry.bounds
        self.cache: dict[tuple[int, int], bool] = {}

    def contains(self, lat_i: int, lon_i: int) -> bool:
        key = (lat_i, lon_i)
        if key not in self.cache:
            lon, lat = lon_i / 1e7, lat_i / 1e7
            min_x, min_y, max_x, max_y = self.bounds
            inside = min_x <= lon <= max_x and min_y <= lat <= max_y
            self.cache[key] = inside and self.prepared.contains(shapely.Point(lon, lat))
        return self.cache[key]


@dataclass
class AreaPositions:
    """Positions of nodes inside one area, usable ones indexed by time for the lnk_02 window.

    Coordinates are the box centres the node sent, in EPSG:4326 degrees.
    """

    positioned_anywhere: set[int] = field(default_factory=set)
    any_nodes: set[int] = field(default_factory=set)
    coarse_nodes: set[int] = field(default_factory=set)
    usable_nodes: set[int] = field(default_factory=set)
    bits: Counter[int] = field(default_factory=Counter)
    usable: dict[int, list[tuple[float, float, float, int]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    stationary: dict[int, tuple[float, float, int]] = field(default_factory=dict)

    def near(
        self, node: int, at_s: float, window_s: float, stationary: bool = False
    ) -> tuple[float, float, int] | None:
        """Usable (lon, lat, bits) of node closest in time to at_s within the window, else None.

        With stationary set, a node whose every position in the capture is the
        same box gets that box for every receipt (addendum rule 2b).
        """
        if stationary and node in self.stationary:
            return self.stationary[node]
        rows = self.usable.get(node)
        if not rows:
            return None
        index = bisect.bisect_left(rows, (at_s, -math.inf, -math.inf, 0))
        best = None
        for row in rows[max(0, index - 1) : index + 1]:
            gap = abs(row[0] - at_s)
            if gap <= window_s and (best is None or gap < best[0]):
                best = (gap, (row[1], row[2], row[3]))
        return None if best is None else best[1]

    def why_missing(self, node: int) -> str:
        """Reason a node has no usable position in the window, for the lnk_02 breakdown."""
        if node not in self.positioned_anywhere:
            return "no position in capture"
        if node not in self.any_nodes:
            return "position only outside the area"
        if node not in self.usable_nodes:
            return "below 15 bits"
        return "usable but outside the window"


def area_positions(tally: Tally, area: Area) -> AreaPositions:
    result = AreaPositions(positioned_anywhere=tally.positioned_senders)
    for node, at_s, lat_i, lon_i, bits, source in tally.positions:
        if not area.contains(lat_i, lon_i):
            continue
        result.any_nodes.add(node)
        if source == "position":
            result.bits[bits] += 1
        if bits >= COARSE_BITS:
            result.coarse_nodes.add(node)
        if bits >= USABLE_BITS:
            result.usable_nodes.add(node)
            result.usable[node].append((at_s, lon_i / 1e7, lat_i / 1e7, bits))
    for node, rows in result.usable.items():
        rows.sort()
        if len(tally.boxes.get(node, ())) == 1:
            result.stationary[node] = (rows[0][1], rows[0][2], rows[0][3])
    return result


def boxes_overlap(a: tuple[float, float, int], b: tuple[float, float, int]) -> bool:
    """Whether two position boxes (lon, lat, bits) share any ground.

    A box at N bits spans 2 ** (32 - N) units of 1e-7 degrees on each axis,
    so two boxes overlap when the centres differ by less than the sum of the
    half-widths on both axes.
    """
    reach_deg = (2 ** (31 - a[2]) + 2 ** (31 - b[2])) * 1e-7
    return abs(a[0] - b[0]) < reach_deg and abs(a[1] - b[1]) < reach_deg


def box_diagonal_m(bits: int) -> float:
    north_south_m, east_west_m = box_half_width_m(bits, ST_LOUIS_LATITUDE_DEG)
    return 2.0 * math.hypot(north_south_m, east_west_m)


def by_low_byte(nodes: set[int]) -> dict[int, set[int]]:
    table: dict[int, set[int]] = defaultdict(set)
    for node in nodes:
        table[node & 0xFF].add(node)
    return table


def resolve_relay(sender: int, relay: int, candidates: dict[int, set[int]], strict: bool) -> int:
    """Relayer from relay_node among senders the gateway heard by radio; 0 when not one."""
    if not strict and relay == sender & 0xFF:
        return sender
    found = candidates.get(relay, set())
    return next(iter(found)) if len(found) == 1 else 0


def resolve_neighbor(sender: int, relay: int, neighbors: dict[int, set[int]]) -> int:
    """Relayer from relay_node among the gateway's NeighborInfo neighbours; 0 when not one."""
    found = neighbors.get(relay, set())
    if relay == sender & 0xFF and sender in found:
        return sender
    return next(iter(found)) if len(found) == 1 else 0


def resolve_all(
    sender: int, relay: int, candidates: dict[int, set[int]], neighbors: dict[int, set[int]]
) -> dict[str, int]:
    by_relay = resolve_relay(sender, relay, candidates, strict=False)
    by_neighbor = resolve_neighbor(sender, relay, neighbors)
    agree = not (by_relay and by_neighbor and by_relay != by_neighbor)
    return {
        "relay_node": by_relay,
        "neighbor_info": by_neighbor,
        "combined": (by_relay or by_neighbor) if agree else 0,
        "relay_node_strict": resolve_relay(sender, relay, candidates, strict=True),
    }


@dataclass
class RuleResult:
    """Funnel counts and link-hour windows for one link rule in one area."""

    funnel: Counter[str] = field(default_factory=Counter)
    windows: dict[tuple[int, int, int], list[float]] = field(default_factory=dict)
    unpositioned_relayers: set[int] = field(default_factory=set)
    resolved_relayers: set[int] = field(default_factory=set)
    overlap_diagonals_m: list[float] = field(default_factory=list)
    gateway_only: dict[tuple[int, int, int], int] = field(default_factory=dict)
    gateway_only_roots: dict[tuple[int, int, int], Counter[str]] = field(default_factory=dict)

    def gateway_only_hours(self, roots: frozenset[str] | None = None) -> list[tuple[int, int, int]]:
        """Gateway-only link-hours; with roots set, counting only receipts under those roots."""
        if roots is None:
            return [key for key, n in self.gateway_only.items() if n >= MIN_PACKETS_PER_HOUR]
        return [
            key
            for key, by_root in self.gateway_only_roots.items()
            if sum(n for root, n in by_root.items() if root in roots) >= MIN_PACKETS_PER_HOUR
        ]

    def add(self, relayer: int, gateway: int, at_s: float, distance_m: float) -> None:
        key = (relayer, gateway, int(at_s // 3600))
        self.windows.setdefault(key, []).append(distance_m)

    def link_hours(
        self, min_distance_m: float, min_packets: int = MIN_PACKETS_PER_HOUR
    ) -> list[tuple[int, int, int]]:
        """Link-hour keys with enough packets whose median distance meets the minimum."""
        return [
            key
            for key, distances in self.windows.items()
            if len(distances) >= min_packets and statistics.median(distances) >= min_distance_m
        ]


def neighbors_by_reporter(tally: Tally) -> dict[int, dict[int, set[int]]]:
    reported: dict[int, set[int]] = defaultdict(set)
    for _, reporter, neighbors in tally.neighbor_reports:
        reported[reporter].update(node for node, _ in neighbors if node != reporter)
    return {reporter: by_low_byte(nodes) for reporter, nodes in reported.items()}


def area_resolutions(
    tally: Tally, positions: AreaPositions
) -> list[tuple[float, int, bool, dict[str, int], str]]:
    """Radio receipts at gateways positioned in the area, with the relayer under each rule."""
    gateways = positions.any_nodes & set(tally.heard)
    candidates = {gateway: by_low_byte(tally.heard[gateway]) for gateway in gateways}
    neighbors = neighbors_by_reporter(tally)
    rows = []
    for (gateway, sender, _), (at_s, relay, signal_ok, root) in tally.receipts.items():
        if gateway in gateways:
            resolved = resolve_all(sender, relay, candidates[gateway], neighbors.get(gateway, {}))
            rows.append((at_s, gateway, signal_ok, resolved, root))
    return rows


def apply_rules(
    tally: Tally,
    positions: AreaPositions,
    window_s: float = POSITION_WINDOW_S,
    stationary: bool = False,
) -> dict[str, RuleResult]:
    """Run lnk_06, lnk_01, lnk_03, lnk_02 and lnk_05 for each rule; lnk_04 is applied on output."""
    results = {rule: RuleResult() for rule in RULES}
    for at_s, gateway, signal_ok, resolved, root in area_resolutions(tally, positions):
        for rule, relayer in resolved.items():
            receipt = (relayer, gateway, at_s, signal_ok, root)
            check_receipt(results[rule], positions, receipt, (window_s, stationary))
    return results


def check_receipt(
    result: RuleResult,
    positions: AreaPositions,
    receipt: tuple[int, int, float, bool, str],
    window: tuple[float, bool],
) -> None:
    relayer, gateway, at_s, signal_ok, root = receipt
    window_s, stationary = window
    funnel = result.funnel
    funnel["1 radio receipts with relay_node at area gateways"] += 1
    if not signal_ok:
        funnel["1x lnk_06 fail: rx_rssi or rx_snr missing or out of range"] += 1
        return
    funnel["2 lnk_06 signal in range"] += 1
    if not relayer:
        funnel["2x lnk_01 fail: relayer not resolved"] += 1
        return
    funnel["3 lnk_01 relayer resolved"] += 1
    if relayer == gateway:
        funnel["3x lnk_03 fail: relayer is the gateway"] += 1
        return
    funnel["4 lnk_03 different nodes"] += 1
    result.resolved_relayers.add(relayer)
    if positions.near(gateway, at_s, window_s, stationary) is not None:
        group = (relayer, gateway, int(at_s // 3600))
        result.gateway_only[group] = result.gateway_only.get(group, 0) + 1
        result.gateway_only_roots.setdefault(group, Counter())[root] += 1
    found: list[tuple[float, float, int]] = []
    for role, node in (("relayer", relayer), ("gateway", gateway)):
        location = positions.near(node, at_s, window_s, stationary)
        if location is None:
            funnel[f"4x lnk_02 fail: {role}, {positions.why_missing(node)}"] += 1
            if role == "relayer" and node not in positions.positioned_anywhere:
                result.unpositioned_relayers.add(node)
            return
        found.append(location)
    funnel["5 lnk_02 both usable within the window"] += 1
    check_distance(result, (relayer, gateway, at_s), found[0], found[1])


def check_distance(
    result: RuleResult,
    link: tuple[int, int, float],
    relayer_at: tuple[float, float, int],
    gateway_at: tuple[float, float, int],
) -> None:
    """lnk_05 on the WGS84 geodesic distance between two box centres, (lon, lat, bits).

    Overlapping boxes have no single distance: it lies between 0 and the
    larger box's diagonal. They get their own funnel row and never a
    link-hour, since any minimum distance rule fails on the lower bound.
    """
    distance_m = GEOD.inv(relayer_at[0], relayer_at[1], gateway_at[0], gateway_at[1])[2]
    if boxes_overlap(relayer_at, gateway_at):
        result.funnel["5x lnk_05 fail: boxes overlap, distance 0 m to the box diagonal"] += 1
        result.overlap_diagonals_m.append(box_diagonal_m(min(relayer_at[2], gateway_at[2])))
    elif distance_m < MIN_DISTANCE_M:
        result.funnel["5x lnk_05 fail: under 500 m"] += 1
    elif distance_m > MAX_DISTANCE_M:
        result.funnel["5x lnk_05 fail: over 60 km"] += 1
    else:
        result.funnel["6 lnk_05 500 m to 60 km"] += 1
        result.add(*link, distance_m)


def print_table(title: str, rows: list[tuple[str, ...]], header: tuple[str, ...]) -> None:
    print(f"\n## {title}\n")
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join(" --- " for _ in header) + "|")
    for row in rows:
        print("| " + " | ".join(str(cell) for cell in row) + " |")


def pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:.1f}%" if whole else "n/a"


def counts_of(tally: Tally, *prefix: str) -> Counter[str]:
    """Counter of the last key element for every count whose key starts with prefix."""
    width = len(prefix)
    return Counter(
        {
            key[width]: n
            for key, n in tally.counts.items()
            if key[:width] == prefix and len(key) == width + 1
        }
    )


def utc_label(at_s: float) -> str:
    return f"{datetime.fromtimestamp(at_s, UTC):%Y-%m-%dT%H:%M:%S}Z"


def report_coverage(tally: Tally) -> None:
    """Segments, observed hours by local day class, and the denominators every rate uses."""
    coverage = tally.coverage()
    rows = []
    for index, (start, end) in enumerate(coverage.segments, 1):
        label = "main" if index == 1 else f"fragment {index - 1}"
        rows.append(
            (str(index), label, utc_label(start), utc_label(end), f"{(end - start) / 3600:.3f}")
        )
    print_table(
        f"Capture segments (a gap over {SEGMENT_GAP_S // 60} minutes starts a new one)",
        rows,
        ("segment", "label", "first record", "last record", "observed h"),
    )
    exposure = coverage.class_hours()
    print(
        f"\nObserved hours: {coverage.hours():.3f}; local weekday (Mon to Fri, CDT)"
        f" {exposure['weekday']:.3f} h, local weekend {exposure['weekend']:.3f} h."
        f" First record to last record: {(tally.last_s - tally.first_s) / 3600:.2f} h of wall"
        " clock, which no rate uses."
    )


def report_volume(tally: Tally) -> None:
    """JSON lines bytes by topic root and copy class, per observed hour and per day."""
    hours = tally.hours()
    sizes = {key[1:]: n for key, n in tally.counts.items() if key[0] == "volume_bytes"}
    records = {key[1:]: n for key, n in tally.counts.items() if key[0] == "volume_records"}
    total = sum(sizes.values())
    rows = []
    for key in sorted(sizes, key=lambda k: -sizes[k]):
        rows.append(
            (
                key[0],
                key[1],
                str(records[key]),
                f"{sizes[key] / 1e6:.1f}",
                pct(sizes[key], total),
                f"{sizes[key] / hours / 1e6:.1f}",
                f"{sizes[key] / hours * 24 / 1e9:.3f}",
            )
        )
    rows.append(
        (
            "all",
            "all",
            str(sum(records.values())),
            f"{total / 1e6:.1f}",
            "100.0%",
            f"{total / hours / 1e6:.1f}",
            f"{total / hours * 24 / 1e9:.3f}",
        )
    )
    print_table(
        f"Volume, uncompressed JSON lines, over {hours:.2f} observed h",
        rows,
        (
            "topic root",
            "copy class",
            "records",
            "MB",
            "share of bytes",
            "MB per hour",
            "GB per day",
        ),
    )
    keys = [key for key in tally.counts if key[0] == "internet_key"]
    per_hour = Counter(key[1] for key in keys)
    median = statistics.median(per_hour.values()) if per_hour else 0
    print(
        f"\nInternet copies as counts per (hour, topic, gateway): {len(keys)} rows over"
        f" {len(per_hour)} clock hours, median {median:.0f} rows per hour,"
        f" {len(keys) / hours * 24:.0f} rows per day"
    )


def report_overview(tally: Tally) -> None:
    hours = tally.hours()
    report_coverage(tally)
    report_volume(tally)
    kinds = counts_of(tally, "kind")
    rows = [(k, str(n), f"{n / hours:.0f}") for k, n in kinds.most_common()]
    print_table("Records by topic kind", rows, ("kind", "records", "per hour"))
    for prefix, title, label in (
        ("status", "Decode status (ing_03 set), all envelopes", "status"),
        ("reason", "Decode status by reason, all envelopes", "status: reason"),
        ("consent", "OK_TO_MQTT bit on packets that arrived with a payload", "ok_to_mqtt"),
    ):
        table = counts_of(tally, prefix)
        total = sum(table.values())
        rows = [(k, str(n), pct(n, total)) for k, n in table.most_common()]
        print_table(title, rows, (label, "packets", "share"))
    report_senders_and_ports(tally)


def report_senders_and_ports(tally: Tally) -> None:
    labels = {1: "stripped only", 2: "payload only", 3: "both"}
    mix = Counter(labels[bits] for bits in tally.sender_kind.values())
    total = len(tally.sender_kind)
    rows = [(kind, str(n), pct(n, total)) for kind, n in mix.most_common()]
    print_table("Senders by payload arrival", rows, ("sender kind", "senders", "share"))
    ports = counts_of(tally, "port")
    total_ports = sum(ports.values())
    rows = []
    for port, n in ports.most_common():
        number = int(port)
        known = number in portnums_pb2.PortNum.values()
        name = portnums_pb2.PortNum.Name(number) if known else port
        rows.append((name, str(n), pct(n, total_ports)))
    unknown = tally.counts[("status", "unknown_port")]
    title = f"Decoded portnums: {len(ports)} distinct, unknown_port {unknown}"
    print_table(title, rows, ("portnum", "packets", "share of decoded"))
    report_receipt_definitions(tally)


def report_receipt_definitions(tally: Tally) -> None:
    """The counts behind each reading of 'radio receipt' (addendum item 1)."""
    rf = counts_of(tally, "mode", "rf")
    rows = [
        ("radio deliveries (envelopes with via_mqtt false)", str(rf["envelopes"])),
        ("radio deliveries, gateway's own packets excluded", str(rf["envelopes"] - rf["own"])),
        (
            "distinct radio receipts (gateway, sender, packet id), own excluded",
            str(len(tally.radio_keys)),
        ),
        (
            "of which with relay_node set (the receipts every later figure uses)",
            str(len(tally.receipts)),
        ),
    ]
    print_table("Radio receipt definitions", rows, ("definition", "count"))


def report_modes(tally: Tally) -> None:
    """Measurement (a), part one: envelopes split by via_mqtt, radio envelopes first."""
    rf, mqtt = counts_of(tally, "mode", "rf"), counts_of(tally, "mode", "mqtt")
    rf_not_own = counts_of(tally, "mode", "rf_not_own")
    rows = []
    for label, part, whole in (
        ("envelopes with a parsed header", "envelopes", "envelopes"),
        ("header-only (payload stripped)", "stripped", "envelopes"),
        ("relay_node non-zero", "relay_set", "envelopes"),
        ("relay_node non-zero, among header-only", "relay_set_stripped", "stripped"),
        ("relay_node equals the sender's own low byte, among relayed", "self_relay", "relay_set"),
        ("gateway's own packets", "own", "envelopes"),
        ("hop_limit is zero", "hop_limit_zero", "envelopes"),
    ):
        cells = [label]
        for table in (rf, rf_not_own, mqtt):
            cells += [str(table[part]), pct(table[part], table[whole])]
        rows.append(tuple(cells))
    header = (
        "measure",
        "radio",
        "share",
        "radio, gateway's own excluded",
        "share",
        "via_mqtt true",
        "share",
    )
    print_table(
        "(a) Envelopes by via_mqtt (radio envelopes are the denominator for (a))", rows, header
    )
    rf_status, mqtt_status = (
        counts_of(tally, "mode_reason", "rf"),
        counts_of(tally, "mode_reason", "mqtt"),
    )
    keys = sorted(set(rf_status) | set(mqtt_status), key=lambda k: -(rf_status[k] + mqtt_status[k]))
    rows = [
        (
            k,
            str(rf_status[k]),
            pct(rf_status[k], rf["envelopes"]),
            str(mqtt_status[k]),
            pct(mqtt_status[k], mqtt["envelopes"]),
        )
        for k in keys
    ]
    print_table(
        "Decode status by via_mqtt", rows, ("status: reason", "radio", "share", "via_mqtt", "share")
    )


def report_relay_resolution(tally: Tally) -> None:
    """Measurement (a), part two: how the low byte resolves on distinct radio receipts."""
    everyone = Counter(sender & 0xFF for sender in tally.sender_kind)
    per_gateway = {
        gateway: Counter(s & 0xFF for s in senders) for gateway, senders in tally.heard.items()
    }
    outcome: dict[str, Counter[str]] = {"capture": Counter(), "gateway": Counter()}
    for (gateway, _, _), (_, relay, _, _) in tally.receipts.items():
        for scope, table in (("capture", everyone), ("gateway", per_gateway[gateway])):
            matches = table.get(relay, 0)
            outcome[scope]["one" if matches == 1 else "many" if matches > 1 else "none"] += 1
    rows = []
    for scope, label in (
        ("capture", "all senders in the capture"),
        ("gateway", "senders the same gateway heard by radio"),
    ):
        total = sum(outcome[scope].values())
        for result in ("one", "many", "none"):
            rows.append(
                (label, result, str(outcome[scope][result]), pct(outcome[scope][result], total))
            )
    title = (
        "(a) relay_node low byte resolution per distinct radio receipt"
        " (gateway, sender, packet id),"
        " gateway's own packets excluded"
    )
    print_table(title, rows, ("candidate set", "matches", "receipts", "share"))


def report_neighbor(tally: Tally) -> None:
    """Measurement (b): NeighborInfo volume and distinct pairs."""
    reports = tally.neighbor_reports
    pairs = {(node, reporter) for _, reporter, neighbors in reports for node, _ in neighbors}
    snrs = [snr for _, _, neighbors in reports for _, snr in neighbors]
    rows = [
        ("NeighborInfo packets", str(len(reports))),
        ("packets per hour", f"{len(reports) / tally.hours():.1f}"),
        ("distinct reporters", str(len({reporter for _, reporter, _ in reports}))),
        ("distinct (neighbour, reporter) pairs", str(len(pairs))),
        ("median SNR reported (dB)", f"{statistics.median(snrs):.1f}" if snrs else "n/a"),
        ("distinct portnums that ever arrived decoded", str(len(counts_of(tally, "port")))),
        ("packets with status unknown_port", str(tally.counts[("status", "unknown_port")])),
    ]
    print_table("(b) NeighborInfo, national", rows, ("measure", "value"))


def precision_rows(bits: Counter[int]) -> list[tuple[str, ...]]:
    total = sum(bits.values())
    rows = []
    for value in sorted(bits):
        north_south_m, east_west_m = (
            box_half_width_m(value, ST_LOUIS_LATITUDE_DEG) if 0 < value <= 32 else (0.0, 0.0)
        )
        share = pct(bits[value], total)
        rows.append(
            (str(value), str(bits[value]), share, f"{north_south_m:.0f}", f"{east_west_m:.0f}")
        )
    return rows


def report_precision(tally: Tally) -> None:
    """Measurement (c): precision_bits distribution, national."""
    header = ("bits", "packets", "share", "half-width N-S m", "half-width E-W m")
    for prefix, title in (
        (
            "position_bits",
            "(c) precision_bits on Position packets, national (half-widths at 38.63 N)",
        ),
        ("map_bits", "(c) position_precision on MapReport packets, national"),
    ):
        bits = Counter({int(k): n for k, n in counts_of(tally, prefix).items()})
        print_table(title, precision_rows(bits), header)
    precise = sum(n for k, n in counts_of(tally, "position_bits").items() if int(k) >= PRECISE_BITS)
    print(
        f"\nPosition packets with precision_bits >= {PRECISE_BITS} (pos_04 precise_source):"
        f" {precise}, from {len(tally.precise_senders)} distinct nodes (Position or MapReport)"
    )


def report_regional_topics(tally: Tally) -> None:
    """Traffic under the St. Louis, Missouri and Illinois topic segments of the public broker."""
    hours = tally.hours()
    rows = []
    for segment in REGIONAL_SEGMENTS:
        packets = tally.counts[("regional", segment, "packets")]
        rows.append(
            (
                f"msh/US/{segment}",
                str(packets),
                f"{packets / hours:.0f}",
                pct(tally.counts[("regional", segment, "rf")], packets),
                str(len(tally.regional_sets.get((segment, "gateways"), ()))),
                str(len(tally.regional_sets.get((segment, "senders"), ()))),
                pct(tally.counts[("regional", segment, "relayed")], packets),
                pct(tally.counts[("regional", segment, "payload")], packets),
                str(tally.counts[("regional", segment, "positions")]),
            )
        )
    header = (
        "topic root",
        "packets",
        "per hour",
        "radio",
        "gateways",
        "senders",
        "relayed",
        "with payload",
        "positions",
    )
    print_table("Regional topic segments on the public broker (e topics)", rows, header)


def report_area_counts(tally: Tally, area: Area, positions: AreaPositions) -> None:
    gateways = positions.any_nodes & set(tally.heard)
    heard = set().union(*(tally.heard[g] for g in gateways)) if gateways else set()
    receipts = sum(1 for (g, _, _) in tally.receipts if g in gateways)
    reporters = {
        reporter for _, reporter, _ in tally.neighbor_reports if reporter in positions.any_nodes
    }
    rows = [
        ("nodes with a position inside (any precision)", str(len(positions.any_nodes))),
        ("of which coarse or better (bits >= 12)", str(len(positions.coarse_nodes))),
        ("of which usable (bits >= 15)", str(len(positions.usable_nodes))),
        ("gateways positioned inside that heard anything by radio", str(len(gateways))),
        ("of which usable", str(len(gateways & positions.usable_nodes))),
        ("distinct senders those gateways heard by radio", str(len(heard))),
        ("radio receipts with relay_node at those gateways", str(receipts)),
        ("NeighborInfo reporters positioned inside", str(len(reporters))),
    ]
    print_table(f"Counts inside {area.name}", rows, ("measure", "value"))
    header = ("bits", "packets", "share", "half-width N-S m", "half-width E-W m")
    print_table(
        f"(c) precision_bits on Position packets inside {area.name}",
        precision_rows(positions.bits),
        header,
    )


def group_cells(keys: list[tuple[int, int, int]], coverage: Coverage) -> list[str]:
    """Observed count, hourly rate, weekday-weighted week, distinct relayers, gateways, pairs."""
    pairs = {(k[0], k[1]) for k in keys}
    rates = coverage.rates(k[2] for k in keys)
    return [
        str(len(keys)),
        f"{rates.per_hour:.2f}",
        f"{rates.week_weighted:.0f}",
        str(len({p[0] for p in pairs})),
        str(len({p[1] for p in pairs})),
        str(len(pairs)),
    ]


YIELD_HEADER = (
    "500 m observed",
    "per hour",
    "per week",
    "relayers",
    "gateways",
    "pairs",
    "3 km observed",
    "per hour",
    "per week",
    "relayers",
    "gateways",
    "pairs",
)


def denominator_note(coverage: Coverage) -> str:
    exposure = coverage.class_hours()
    return (
        f"per hour = observed / {coverage.hours():.2f} observed h; per week = weekday rate"
        f" ({exposure['weekday']:.2f} h) x 120 + weekend rate ({exposure['weekend']:.2f} h) x 48"
    )


def yield_cells(
    result: RuleResult, coverage: Coverage, min_packets: int = MIN_PACKETS_PER_HOUR
) -> list[str]:
    cells: list[str] = []
    for minimum in (MIN_DISTANCE_M, VALIDATION_DISTANCE_M):
        cells += group_cells(result.link_hours(minimum, min_packets), coverage)
    return cells


def gateway_only_cells(result: RuleResult, coverage: Coverage) -> list[str]:
    """Link-hours when only the gateway is positioned: no distance, so one set of cells."""
    return group_cells(result.gateway_only_hours(), coverage) + ["n/a"] * 6


def report_yield(tally: Tally, area: Area, results: dict[str, RuleResult]) -> float:
    """Measurement (e): link-hours per rule; returns the combined 3 km weekly figure."""
    coverage = tally.coverage()
    rows = [(rule, *yield_cells(results[rule], coverage)) for rule in RULES]
    title = (
        f"(e) Link-hours inside {area.name}, 30-minute position rule, 3 km on the median;"
        f" {denominator_note(coverage)}"
    )
    print_table(title, rows, ("rule", *YIELD_HEADER))
    steps = sorted(set().union(*(results[r].funnel for r in RULES)))
    funnel_rows = [(step, *[str(results[r].funnel[step]) for r in RULES]) for step in steps]
    print_table(
        f"(e) Funnel inside {area.name}, distinct radio receipts", funnel_rows, ("step", *RULES)
    )
    report_unpositioned(tally, results["combined"])
    keys = results["combined"].link_hours(VALIDATION_DISTANCE_M)
    return coverage.rates(k[2] for k in keys).week_weighted


def report_unpositioned(tally: Tally, combined: RuleResult) -> None:
    relayers = combined.unpositioned_relayers
    other_payload = relayers & tally.payload_senders
    print(
        "\nCombined rule, relayers failing lnk_02 with no decodable position in the capture:"
        f" {len(relayers)}, of which sent other decoded packets"
        f" (NodeInfo, Telemetry, Routing and so on): {len(other_payload)}"
    )
    diagonals = sorted(combined.overlap_diagonals_m)
    if diagonals:
        print(
            f"Overlapping-box receipts: {len(diagonals)}; distance range upper bound (the larger"
            f" box's diagonal) min {diagonals[0]:.0f} m, median"
            f" {statistics.median(diagonals):.0f} m, max {diagonals[-1]:.0f} m"
        )


def report_position_rules(tally: Tally, area: Area, positions: AreaPositions) -> None:
    """The three position rule options of ADR 0003, combined link rule, one row each."""
    coverage = tally.coverage()
    thirty = apply_rules(tally, positions)["combined"]
    stationary = apply_rules(tally, positions, stationary=True)["combined"]
    rows = [
        ("keep 30 minutes (lnk_02 as written)", *yield_cells(thirty, coverage)),
        (
            "stationarity window (one box all capture, else 30 minutes)",
            *yield_cells(stationary, coverage),
        ),
        (
            "gateway-only fallback (relayer unpositioned, no distance)",
            *gateway_only_cells(thirty, coverage),
        ),
    ]
    print_table(
        f"Position rule options inside {area.name}, combined link rule;"
        f" {denominator_note(coverage)}",
        rows,
        ("position rule", *YIELD_HEADER),
    )
    print(
        f"Stationary usable nodes inside {area.name}: {len(positions.stationary)} of"
        f" {len(positions.usable_nodes)} usable (one box for every position in the capture)"
    )
    report_headline(tally, area, positions, (thirty, stationary))
    report_growth(tally, area, thirty, positions)


def report_headline(
    tally: Tally,
    area: Area,
    positions: AreaPositions,
    results: tuple[RuleResult, RuleResult],
) -> None:
    """Hourly rate by local day class and the weekday-weighted week, per position rule."""
    coverage = tally.coverage()
    thirty, stationary = results
    measures = [
        ("keep 30 minutes, 500 m", thirty.link_hours(MIN_DISTANCE_M)),
        ("keep 30 minutes, 3 km", thirty.link_hours(VALIDATION_DISTANCE_M)),
        ("stationarity window, 500 m", stationary.link_hours(MIN_DISTANCE_M)),
        ("stationarity window, 3 km", stationary.link_hours(VALIDATION_DISTANCE_M)),
        ("gateway-only fallback", thirty.gateway_only_hours()),
        ("lnk_02 and lnk_05 ignored (diagnostic)", position_free_groups(tally, positions)),
    ]
    exposure = coverage.class_hours()
    rows = []
    for label, keys in measures:
        r = coverage.rates(k[2] for k in keys)
        rows.append(
            (
                label,
                str(r.observed),
                f"{r.per_hour:.2f}",
                f"{r.weekday_per_hour:.2f}",
                f"{r.weekend_per_hour:.2f}",
                f"{r.week_flat:.0f}",
                f"{r.week_weighted:.0f}",
            )
        )
    header = (
        "link-hours, combined rule",
        "observed",
        f"per hour ({coverage.hours():.2f} h)",
        f"weekday per hour ({exposure['weekday']:.2f} h)",
        f"weekend per hour ({exposure['weekend']:.2f} h)",
        "per week, flat (x168)",
        "per week, weekday-weighted (x120, x48)",
    )
    print_table(f"Headline: link-hour rates inside {area.name}", rows, header)


def cumulative_groups(
    keys: list[tuple[int, int, int]], coverage: Coverage
) -> list[tuple[str, ...]]:
    """Distinct groups by observed hours elapsed, so a gap between segments is not counted."""
    rows = []
    for hours in (*(h for h in GROWTH_HOURS if h < coverage.hours()), coverage.hours()):
        cutoff = [k for k in keys if coverage.overlap_h(-math.inf, (k[2] + 1) * 3600) <= hours]
        pairs = {(k[0], k[1]) for k in cutoff}
        rows.append(
            (
                f"{hours:g}" if isinstance(hours, int) else f"{hours:.2f} (all)",
                str(len(cutoff)),
                str(len({p[0] for p in pairs})),
                str(len({p[1] for p in pairs})),
                str(len(pairs)),
            )
        )
    return rows


def report_growth(tally: Tally, area: Area, combined: RuleResult, positions: AreaPositions) -> None:
    """Addendum 2d: cumulative distinct groups by observed hours."""
    header = ("observed hours", "link-hours", "relayers", "gateways", "pairs")
    coverage = tally.coverage()
    contract = combined.link_hours(MIN_DISTANCE_M)
    print_table(
        f"Group growth inside {area.name}, contract rules at 500 m, cumulative",
        cumulative_groups(contract, coverage),
        header,
    )
    free = position_free_groups(tally, positions)
    print_table(
        f"Group growth inside {area.name}, lnk_02 and lnk_05 ignored, cumulative",
        cumulative_groups(free, coverage),
        header,
    )
    print_table(
        f"Group growth inside {area.name}, gateway-only fallback, cumulative",
        cumulative_groups(combined.gateway_only_hours(), coverage),
        header,
    )


def report_first_position(tally: Tally, area: Area, relayers: set[int]) -> None:
    """Addendum 2a: time from a relayer's first radio receipt to its first usable position."""
    columns = {
        f"relayers resolved inside {area.name}": relayers & set(tally.first_heard),
        "all senders heard by radio": set(tally.first_heard),
    }
    table: dict[str, list[str]] = defaultdict(list)
    for nodes in columns.values():
        never = [n for n in nodes if n not in tally.first_position]
        coarse_only = [
            n for n in nodes if n in tally.first_position and n not in tally.first_usable
        ]
        waits_h = sorted(
            max(0.0, tally.first_usable[n] - tally.first_heard[n]) / 3600.0
            for n in nodes
            if n in tally.first_usable
        )
        before = sum(
            1
            for n in nodes
            if n in tally.first_usable and tally.first_usable[n] < tally.first_heard[n]
        )
        table["nodes"].append(str(len(nodes)))
        none = f"{len(never)} ({pct(len(never), len(nodes))})"
        table["no decodable position in the capture"].append(none)
        table["sent positions only below 15 bits"].append(
            f"{len(coarse_only)} ({pct(len(coarse_only), len(nodes))})"
        )
        table["reached a usable position"].append(
            f"{len(waits_h)} ({pct(len(waits_h), len(nodes))})"
        )
        table["of which before their first radio receipt"].append(str(before))
        for label, q in (("p25", 0.25), ("p50", 0.5), ("p75", 0.75), ("p90", 0.9), ("max", 1.0)):
            value = waits_h[min(len(waits_h) - 1, int(q * len(waits_h)))] if waits_h else None
            table[f"hours to first usable, {label}"].append(
                f"{value:.1f}" if value is not None else "n/a"
            )
        lower = 0.0
        for upper in FIRST_POSITION_BINS_H:
            count = sum(1 for w in waits_h if lower <= w < upper)
            table[f"histogram, {lower:g} to {upper} h"].append(str(count))
            lower = float(upper)
    rows = [(label, *cells) for label, cells in table.items()]
    print_table(
        f"(2a) Time to first usable position, {tally.hours():.2f} h of capture",
        rows,
        ("measure", *columns.keys()),
    )


def position_free_groups(
    tally: Tally, positions: AreaPositions, roots: frozenset[str] | None = None
) -> list[tuple[int, int, int]]:
    """Combined-rule (relayer, gateway, hour) groups with 3 receipts, positions ignored.

    With roots set, only receipts that arrived under those topic roots count.
    """
    groups: Counter[tuple[int, int, int]] = Counter()
    for at_s, gateway, signal_ok, resolved, root in area_resolutions(tally, positions):
        relayer = resolved["combined"]
        if roots is not None and root not in roots:
            continue
        if signal_ok and relayer and relayer != gateway:
            groups[(relayer, gateway, int(at_s // 3600))] += 1
    return [key for key, n in groups.items() if n >= MIN_PACKETS_PER_HOUR]


def report_position_free(tally: Tally, area: Area, positions: AreaPositions) -> None:
    """Diagnostic: link-hour groups if lnk_02 and lnk_05 were ignored, combined rule."""
    keys = position_free_groups(tally, positions)
    pairs = {(relayer, gateway) for relayer, gateway, _ in keys}
    usable = positions.usable_nodes
    rates = tally.coverage().rates(k[2] for k in keys)
    rows = [
        ("(relayer, gateway, hour) groups with 3 or more receipts", str(len(keys))),
        (f"per hour, over {rates.hours:.2f} observed h", f"{rates.per_hour:.2f}"),
        ("per week, weekday-weighted", f"{rates.week_weighted:.0f}"),
        ("distinct pairs", str(len(pairs))),
        ("distinct relayers", str(len({p[0] for p in pairs}))),
        ("distinct gateways", str(len({p[1] for p in pairs}))),
        (
            "pairs whose relayer sent no decodable position in the capture",
            str(sum(1 for r, _ in pairs if r not in positions.positioned_anywhere)),
        ),
        (
            "pairs with both ends ever usable in the area",
            str(sum(1 for r, g in pairs if r in usable and g in usable)),
        ),
    ]
    print_table(
        f"Diagnostic inside {area.name}: lnk_02 and lnk_05 ignored, combined rule",
        rows,
        ("measure", "value"),
    )


def report_sensitivity(tally: Tally, area: Area, positions: AreaPositions) -> None:
    """Combined-rule yield if lnk_02 and lnk_04 were relaxed; diagnostic only, not the contract."""
    coverage = tally.coverage()
    rows = []
    for window_s, window_label in ((POSITION_WINDOW_S, "30 min (contract)"), (86_400, "24 h")):
        combined = apply_rules(tally, positions, window_s)["combined"]
        for min_packets in (MIN_PACKETS_PER_HOUR, 1):
            rows.append(
                (window_label, str(min_packets), *yield_cells(combined, coverage, min_packets))
            )
    header = ("position window", "min packets", *YIELD_HEADER)
    title = (
        f"Sensitivity, combined rule inside {area.name} (not contract rules);"
        f" {denominator_note(coverage)}"
    )
    print_table(title, rows, header)


def hourly_series(
    tally: Tally, positions: AreaPositions, thirty: RuleResult
) -> dict[str, Counter[int]]:
    """Event counts per clock hour, national and inside the area."""
    gateways = positions.any_nodes & set(tally.heard)
    series: dict[str, Counter[int]] = {
        "records": Counter(),
        "radio deliveries": Counter(),
        "internet copies": Counter(),
        "area radio receipts": Counter(),
        "contract 500 m": Counter(k[2] for k in thirty.link_hours(MIN_DISTANCE_M)),
        "gateway-only": Counter(k[2] for k in thirty.gateway_only_hours()),
        "positions ignored": Counter(k[2] for k in position_free_groups(tally, positions)),
    }
    for key, n in tally.counts.items():
        if key[0] == "hour" and key[2] == "records":
            series["records"][key[1]] += n
        elif key[0] == "hour" and key[2] == "e, radio":
            series["radio deliveries"][key[1]] += n
        elif key[0] == "hour" and key[2] == "e, internet copy (via_mqtt)":
            series["internet copies"][key[1]] += n
    for (gateway, _, _), (at_s, _, _, _) in tally.receipts.items():
        if gateway in gateways:
            series["area radio receipts"][int(at_s // 3600)] += 1
    return series


def report_hourly(tally: Tally, area: Area, positions: AreaPositions, thirty: RuleResult) -> None:
    """Hour-by-hour curve and per-local-day rates, so the weekend effect is visible."""
    coverage = tally.coverage()
    series = hourly_series(tally, positions, thirty)
    rows = []
    for hour in coverage.clock_hours():
        observed_h = coverage.hour_h(hour)
        segment = coverage.segment_of(hour)
        rows.append(
            (
                f"{datetime.fromtimestamp(hour * 3600, UTC):%m-%dT%H}Z",
                f"{local_time(hour):%a %H}",
                "main" if segment == 1 else f"fragment {segment - 1}",
                f"{observed_h * 60:.0f}",
                *(str(series[name][hour]) for name in series),
            )
        )
    header = ("hour UTC", "local CDT", "segment", "observed min", *series)
    print_table(f"Hour by hour, national and inside {area.name} (combined rule)", rows, header)
    days: dict[str, list[int]] = defaultdict(list)
    for hour in coverage.clock_hours():
        days[f"{local_time(hour):%a %Y-%m-%d}"].append(hour)
    rows = []
    for day, hours in days.items():
        observed_h = sum(coverage.hour_h(h) for h in hours)
        rates = [sum(series[name][h] for h in hours) / observed_h for name in series]
        rows.append((day, day_class(hours[0]), f"{observed_h:.2f}", *(f"{r:.2f}" for r in rates)))
    print_table(
        f"Per hour by local day, inside {area.name} (counts / observed h of that day)",
        rows,
        ("local day", "class", "observed h", *series),
    )


def report_topic_shares(
    tally: Tally, area: Area, positions: AreaPositions, thirty: RuleResult
) -> None:
    """Share of the area's radio receipts and link-hours by the topic root they arrived under."""
    gateways = positions.any_nodes & set(tally.heard)
    all_receipts = Counter(
        root_class(root) for (g, _, _), root in tally.radio_keys.items() if g in gateways
    )
    relayed = Counter(root_class(v[3]) for (g, _, _), v in tally.receipts.items() if g in gateways)
    total_all, total_relayed = sum(all_receipts.values()), sum(relayed.values())
    rows = [
        (
            cls,
            str(all_receipts[cls]),
            pct(all_receipts[cls], total_all),
            str(relayed[cls]),
            pct(relayed[cls], total_relayed),
        )
        for cls in ROOT_CLASSES
    ]
    print_table(
        f"Radio receipts at gateways inside {area.name}, by topic root",
        rows,
        ("topic root", "radio receipts", "share", "with relay_node", "share"),
    )
    other = Counter(
        root
        for (g, _, _), root in tally.radio_keys.items()
        if g in gateways and root_class(root) == "other msh/US"
    )
    print(
        "Largest roots inside 'other msh/US': "
        + ", ".join(f"msh/US/{root} {n} ({pct(n, total_all)})" for root, n in other.most_common(5))
    )
    report_scope_yield(tally, area, positions, thirty)


def report_scope_yield(
    tally: Tally, area: Area, positions: AreaPositions, thirty: RuleResult
) -> None:
    """Link-hours kept if only receipts under the given roots had been received."""
    coverage = tally.coverage()
    scopes: list[tuple[str, frozenset[str] | None]] = [
        ("msh/US/# (everything)", None),
        ("MO, IL and MeshSTL", frozenset(REGIONAL_SEGMENTS)),
        ("MO only", frozenset({"MO"})),
        ("IL only", frozenset({"IL"})),
        ("MeshSTL only", frozenset({"MeshSTL"})),
    ]
    full_gateway = len(thirty.gateway_only_hours())
    full_free = len(position_free_groups(tally, positions))
    rows = []
    for label, roots in scopes:
        gateway_only = thirty.gateway_only_hours(roots)
        free = position_free_groups(tally, positions, roots)
        rows.append(
            (
                label,
                str(len(gateway_only)),
                pct(len(gateway_only), full_gateway),
                f"{coverage.rates(k[2] for k in gateway_only).week_weighted:.0f}",
                str(len(free)),
                pct(len(free), full_free),
                f"{coverage.rates(k[2] for k in free).week_weighted:.0f}",
            )
        )
    header = (
        "receipts counted from",
        "gateway-only link-hours",
        "share",
        "per week",
        "positions-ignored link-hours",
        "share",
        "per week",
    )
    print_table(
        f"Subscription scope inside {area.name}, receipts filtered after resolution"
        f" (positions and relayer candidates from the whole capture); {denominator_note(coverage)}",
        rows,
        header,
    )


def borrow_positions(tally: Tally, other: Tally) -> None:
    """Add another broker's positions so a relayer silent here can be placed from there."""
    own = set(tally.positioned_senders)
    usable_other = {row[0] for row in other.positions if row[4] >= USABLE_BITS}
    print(
        f"\nPositions borrowed: {len(other.positions)} rows from"
        f" {len(other.positioned_senders)} nodes positioned on the other broker;"
        f" {len(other.positioned_senders - own)} of them have no position on this one,"
        f" {len(usable_other - own)} of those with a usable one inside Missouri plus Illinois"
        " and margin"
    )
    tally.positions.extend(other.positions)
    tally.positioned_senders |= other.positioned_senders
    for node, boxes in other.boxes.items():
        tally.boxes.setdefault(node, set()).update(boxes)


def report_area(tally: Tally, area: Area, metro: bool = False) -> float:
    positions = area_positions(tally, area)
    report_area_counts(tally, area, positions)
    results = apply_rules(tally, positions)
    combined_3km = report_yield(tally, area, results)
    report_first_position(tally, area, results["combined"].resolved_relayers)
    report_position_rules(tally, area, positions)
    if metro:
        report_hourly(tally, area, positions, results["combined"])
        report_topic_shares(tally, area, positions, results["combined"])
    report_position_free(tally, area, positions)
    report_sensitivity(tally, area, positions)
    return combined_3km


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_dir", type=Path)
    parser.add_argument("msa_csv", type=Path, nargs="?")
    parser.add_argument("states_csv", type=Path, nargs="?")
    parser.add_argument("--start", help="ISO 8601 UTC; records before it are skipped")
    parser.add_argument("--end", help="ISO 8601 UTC; records at or after it are skipped")
    parser.add_argument(
        "--roots",
        help="comma-separated topic roots after msh/US (MO,IL,MeshSTL); other records are skipped",
    )
    parser.add_argument(
        "--positions-from",
        type=Path,
        help="another capture whose positions (same window) are added to this one's",
    )
    return parser.parse_args()


def bound_s(value: str | None, default: float) -> float:
    return datetime.fromisoformat(value).timestamp() if value else default


def main() -> None:
    args = parse_args()
    start_s, end_s = bound_s(args.start, -math.inf), bound_s(args.end, math.inf)
    roots = frozenset(args.roots.split(",")) if args.roots else None
    tally = load_capture(args.data_dir, start_s, end_s, roots)
    if not tally.counts:
        print("no capture records found")
        return
    if roots is not None:
        print(f"Topic roots kept: {', '.join(sorted(roots))} (everything else skipped at load)")
    if args.positions_from is not None:
        borrow_positions(tally, load_capture(args.positions_from, start_s, end_s))
    report_overview(tally)
    report_modes(tally)
    report_relay_resolution(tally)
    report_neighbor(tally)
    report_precision(tally)
    report_regional_topics(tally)
    if args.msa_csv is not None:
        msa = Area("the MSA (CBSA 41180)", load_area(args.msa_csv, {"41180"}))
        combined_3km = report_area(tally, msa, metro=True)
        triggered = combined_3km < WIDEN_THRESHOLD_PER_WEEK
        print(
            f"\n(f) Widening check: combined rule at 3 km gives {combined_3km:.0f}"
            f" link-hours per week against a threshold of {WIDEN_THRESHOLD_PER_WEEK};"
            f" triggered: {triggered}"
        )
        if args.states_csv is not None:
            states = Area("Missouri plus Illinois", load_area(args.states_csv, {"29", "17"}))
            report_area(tally, states)


if __name__ == "__main__":
    main()
