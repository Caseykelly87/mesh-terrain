"""Decode and measure a spike capture for ADR 0003.

Reads every capture-*.jsonl file in a directory, one worker process per file,
parses each payload as a ServiceEnvelope, decrypts default-key packets, decodes
Position, NodeInfo, Telemetry, NeighborInfo and MapReport by portnum, and
prints the measurements the Phase 1 spike asks for: (a) relay_node, (b)
NeighborInfo, (c) precision_bits, regional topics, area counts, and (e) the
link-hour yield per link rule for each area given, with the (f) widening check.
Output is aggregate only: no node id, node name or coordinate is printed. It
is not the production decode asset and writes nothing to disk.

Usage: python analyze.py DATA_DIR MSA_CSV [STATES_CSV]
"""

from __future__ import annotations

import base64
import bisect
import csv
import json
import math
import os
import statistics
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

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
WIDEN_THRESHOLD_PER_WEEK = 300
HOURS_PER_WEEK = 168.0
REGIONAL_SEGMENTS = ("MeshSTL", "MO", "IL")
# Positions kept in memory: Missouri plus Illinois with a margin, as
# (lon_min, lat_min, lon_max, lat_max) in EPSG:4326 degrees.
KEEP_BBOX_DEG = (-96.0, 35.8, -86.9, 42.7)
RULES = ("relay_node", "neighbor_info", "combined", "relay_node_strict")
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
    ok_to_mqtt: bool | None = None
    portnum: int | None = None
    position: tuple[int, int, int] | None = None
    map_report: tuple[int, int, int] | None = None
    neighbors: tuple[tuple[int, float], ...] = ()


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
    and the encrypted payload removed (observed in the spike capture); that
    case is reported as "stripped" so it is not confused with a wrong key.
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
    )
    data, parsed.reason = payload_data(packet)
    if data is None:
        parsed.status = "decrypt_failed"
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


@dataclass
class Tally:
    """Aggregates from one or more capture files; small enough to pickle between processes."""

    first_s: float = math.inf
    last_s: float = 0.0
    counts: Counter[tuple[str, ...]] = field(default_factory=Counter)
    sender_kind: dict[int, int] = field(default_factory=dict)
    heard: dict[int, set[int]] = field(default_factory=lambda: defaultdict(set))
    receipts: dict[tuple[int, int, int], tuple[float, int]] = field(default_factory=dict)
    neighbor_reports: list[tuple[float, int, tuple[tuple[int, float], ...]]] = field(
        default_factory=list
    )
    positions: list[tuple[int, float, int, int, int, str]] = field(default_factory=list)
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
        self.add_payload(parsed)
        if parsed.region and parsed.kind == "e":
            self.add_regional(parsed)

    def add_header(self, parsed: Parsed) -> None:
        own = parsed.gateway == parsed.sender
        flags = {
            "headers": True,
            "own": own,
            "relay_set": bool(parsed.relay),
            "self_relay": bool(parsed.relay) and parsed.relay == parsed.sender & 0xFF,
            "hop_limit_zero": parsed.hop_limit == 0,
            "hop_start_set": bool(parsed.hop_start),
            "via_mqtt": parsed.via_mqtt,
            "stripped": parsed.reason == "stripped",
            "relay_set_stripped": bool(parsed.relay) and parsed.reason == "stripped",
        }
        for name, flag in flags.items():
            self.counts[("header", name)] += int(flag)
        bit = 1 if parsed.reason == "stripped" else 2
        self.sender_kind[parsed.sender] = self.sender_kind.get(parsed.sender, 0) | bit
        if parsed.gateway:
            self.heard[parsed.gateway].add(parsed.sender)
            if parsed.relay and not own:
                key = (parsed.gateway, parsed.sender, parsed.packet_id)
                if key not in self.receipts or parsed.at_s < self.receipts[key][0]:
                    self.receipts[key] = (parsed.at_s, parsed.relay)

    def add_payload(self, parsed: Parsed) -> None:
        if parsed.portnum == portnums_pb2.NEIGHBORINFO_APP:
            self.neighbor_reports.append((parsed.at_s, parsed.sender, parsed.neighbors))
        for source, value in (("position", parsed.position), ("map", parsed.map_report)):
            if value is None or not parsed.sender:
                continue
            self.counts[(f"{source}_bits", str(value[2]))] += 1
            if in_keep_bbox(value[0], value[1]):
                row = (parsed.sender, parsed.at_s, value[0], value[1], value[2], source)
                self.positions.append(row)

    def add_regional(self, parsed: Parsed) -> None:
        segment = parsed.region
        relayed = bool(parsed.relay) and parsed.gateway != parsed.sender
        self.counts[("regional", segment, "packets")] += 1
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
        for key, values in other.regional_sets.items():
            self.regional_sets[key] |= values

    def hours(self) -> float:
        return max((self.last_s - self.first_s) / 3600.0, 1e-9)


def tally_file(path: str) -> Tally:
    """Tally one hourly capture file; a partly written last line is counted and skipped."""
    tally = Tally()
    with open(path, encoding="ascii") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                tally.counts[("kind", "unreadable_line")] += 1
                continue
            kind = topic_kind(record["topic"])
            tally.counts[("kind", kind)] += 1
            if kind not in {"e", "map"}:
                continue
            at_s = datetime.fromisoformat(record["received_at"]).timestamp()
            raw = base64.b64decode(record["payload_b64"])
            tally.add(parse_envelope(at_s, kind, topic_region(record["topic"]), raw))
    tally.heard = dict(tally.heard)
    tally.regional_sets = dict(tally.regional_sets)
    return tally


def load_capture(data_dir: Path) -> Tally:
    paths = sorted(str(p) for p in data_dir.glob("capture-*.jsonl"))
    total = Tally()
    workers = max(1, min(len(paths), (os.cpu_count() or 2) - 1, 8))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for part in pool.map(tally_file, paths):
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
    """Positions of nodes inside one area, usable ones indexed by time for the lnk_02 window."""

    any_nodes: set[int] = field(default_factory=set)
    coarse_nodes: set[int] = field(default_factory=set)
    usable_nodes: set[int] = field(default_factory=set)
    bits: Counter[int] = field(default_factory=Counter)
    usable: dict[int, list[tuple[float, float, float]]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def near(self, node: int, at_s: float, window_s: float) -> tuple[float, float] | None:
        """Usable (lon, lat) of node closest in time to at_s within the window, else None."""
        rows = self.usable.get(node)
        if not rows:
            return None
        index = bisect.bisect_left(rows, (at_s, -math.inf, -math.inf))
        best = None
        for row in rows[max(0, index - 1) : index + 1]:
            gap = abs(row[0] - at_s)
            if gap <= window_s and (best is None or gap < best[0]):
                best = (gap, (row[1], row[2]))
        return None if best is None else best[1]


def area_positions(tally: Tally, area: Area) -> AreaPositions:
    result = AreaPositions()
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
            result.usable[node].append((at_s, lon_i / 1e7, lat_i / 1e7))
    for rows in result.usable.values():
        rows.sort()
    return result


def by_low_byte(nodes: set[int]) -> dict[int, set[int]]:
    table: dict[int, set[int]] = defaultdict(set)
    for node in nodes:
        table[node & 0xFF].add(node)
    return table


def resolve_relay(sender: int, relay: int, candidates: dict[int, set[int]], strict: bool) -> int:
    """Relayer from relay_node among senders the gateway heard; 0 when not exactly one."""
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

    def add(self, relayer: int, gateway: int, at_s: float, distance_m: float) -> None:
        key = (relayer, gateway, int(at_s // 3600))
        self.windows.setdefault(key, []).append(distance_m)

    def link_hours(
        self, min_distance_m: float, min_packets: int = MIN_PACKETS_PER_HOUR
    ) -> list[tuple[int, int, int]]:
        return [
            key
            for key, distances in self.windows.items()
            if len(distances) >= min_packets and distances[0] >= min_distance_m
        ]


def neighbors_by_reporter(tally: Tally) -> dict[int, dict[int, set[int]]]:
    reported: dict[int, set[int]] = defaultdict(set)
    for _, reporter, neighbors in tally.neighbor_reports:
        reported[reporter].update(node for node, _ in neighbors if node != reporter)
    return {reporter: by_low_byte(nodes) for reporter, nodes in reported.items()}


def apply_rules(
    tally: Tally, positions: AreaPositions, window_s: float = POSITION_WINDOW_S
) -> dict[str, RuleResult]:
    """Run lnk_01 to lnk_05 for each rule over receipts at gateways positioned in the area."""
    results = {rule: RuleResult() for rule in RULES}
    gateways = positions.any_nodes & set(tally.heard)
    candidates = {gateway: by_low_byte(tally.heard[gateway]) for gateway in gateways}
    neighbors = neighbors_by_reporter(tally)
    for (gateway, sender, _), (at_s, relay) in tally.receipts.items():
        if gateway not in gateways:
            continue
        resolved = resolve_all(sender, relay, candidates[gateway], neighbors.get(gateway, {}))
        for rule, relayer in resolved.items():
            check_receipt(results[rule], positions, (relayer, gateway, at_s), window_s)
    return results


def check_receipt(
    result: RuleResult,
    positions: AreaPositions,
    receipt: tuple[int, int, float],
    window_s: float,
) -> None:
    relayer, gateway, at_s = receipt
    result.funnel["1 relayed receipts at area gateways"] += 1
    if not relayer:
        return
    result.funnel["2 lnk_01 relayer resolved"] += 1
    if relayer == gateway:
        return
    result.funnel["3 lnk_03 different nodes"] += 1
    relayer_at = positions.near(relayer, at_s, window_s)
    gateway_at = positions.near(gateway, at_s, window_s)
    if relayer_at is None or gateway_at is None:
        return
    result.funnel["4 lnk_02 both usable within 30 min"] += 1
    distance_m = GEOD.inv(relayer_at[0], relayer_at[1], gateway_at[0], gateway_at[1])[2]
    if not MIN_DISTANCE_M <= distance_m <= MAX_DISTANCE_M:
        return
    result.funnel["5 lnk_05 500 m to 60 km"] += 1
    result.add(relayer, gateway, at_s, distance_m)


def print_table(title: str, rows: list[tuple[str, ...]], header: tuple[str, ...]) -> None:
    print(f"\n## {title}\n")
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join(" --- " for _ in header) + "|")
    for row in rows:
        print("| " + " | ".join(str(cell) for cell in row) + " |")


def pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:.1f}%" if whole else "n/a"


def counts_of(tally: Tally, prefix: str) -> Counter[str]:
    return Counter({key[1]: n for key, n in tally.counts.items() if key[0] == prefix})


def report_overview(tally: Tally) -> None:
    hours = tally.hours()
    start = datetime.fromtimestamp(tally.first_s, UTC)
    end = datetime.fromtimestamp(tally.last_s, UTC)
    print(f"Capture window: {start:%Y-%m-%dT%H:%M}Z to {end:%Y-%m-%dT%H:%M}Z ({hours:.2f} h)")
    print(f"Weekly scaling factor: 168 / {hours:.2f} = {HOURS_PER_WEEK / hours:.2f}")
    kinds = counts_of(tally, "kind")
    rows = [(k, str(n), f"{n / hours:.0f}") for k, n in kinds.most_common()]
    print_table("Records by topic kind", rows, ("kind", "records", "per hour"))
    for prefix, title, label in (
        ("status", "Decode status (ing_03 set), envelope topics only", "status"),
        ("reason", "Decode status by reason", "status: reason"),
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
    rows = []
    for port, n in ports.most_common(12):
        number = int(port)
        known = number in portnums_pb2.PortNum.values()
        rows.append((portnums_pb2.PortNum.Name(number) if known else port, str(n)))
    print_table("Decoded portnums (top 12)", rows, ("portnum", "packets"))


def report_relay(tally: Tally) -> None:
    """Measurement (a): relay_node presence and how its low byte resolves."""
    header = counts_of(tally, "header")
    headers = header["headers"]
    rows = [
        ("envelopes with a parsed header (sender set)", str(headers), "100%"),
        ("gateway's own packets", str(header["own"]), pct(header["own"], headers)),
        ("relay_node non-zero", str(header["relay_set"]), pct(header["relay_set"], headers)),
        (
            "relay_node equals the sender's own low byte",
            str(header["self_relay"]),
            pct(header["self_relay"], header["relay_set"]) + " of relayed",
        ),
        (
            "hop_limit is zero",
            str(header["hop_limit_zero"]),
            pct(header["hop_limit_zero"], headers),
        ),
        ("hop_start non-zero", str(header["hop_start_set"]), pct(header["hop_start_set"], headers)),
        ("via_mqtt set", str(header["via_mqtt"]), pct(header["via_mqtt"], headers)),
        (
            "relay_node non-zero, header-only (stripped) envelopes",
            str(header["relay_set_stripped"]),
            pct(header["relay_set_stripped"], header["stripped"]) + " of stripped",
        ),
        (
            "relay_node non-zero, envelopes with a payload",
            str(header["relay_set"] - header["relay_set_stripped"]),
            pct(header["relay_set"] - header["relay_set_stripped"], headers - header["stripped"])
            + " of with payload",
        ),
    ]
    print_table(
        "(a) relay_node on envelopes from the e topics", rows, ("measure", "count", "share")
    )
    report_relay_resolution(tally)


def report_relay_resolution(tally: Tally) -> None:
    everyone = Counter(sender & 0xFF for sender in tally.sender_kind)
    per_gateway = {
        gateway: Counter(s & 0xFF for s in senders) for gateway, senders in tally.heard.items()
    }
    outcome: dict[str, Counter[str]] = {"capture": Counter(), "gateway": Counter()}
    for (gateway, _, _), (_, relay) in tally.receipts.items():
        for scope, table in (("capture", everyone), ("gateway", per_gateway[gateway])):
            matches = table.get(relay, 0)
            outcome[scope]["one" if matches == 1 else "many" if matches > 1 else "none"] += 1
    rows = []
    for scope, label in (
        ("capture", "all senders in the capture"),
        ("gateway", "senders heard by the same gateway"),
    ):
        total = sum(outcome[scope].values())
        for result in ("one", "many", "none"):
            rows.append(
                (label, result, str(outcome[scope][result]), pct(outcome[scope][result], total))
            )
    print_table(
        "(a) relay_node low byte resolution per distinct (gateway, sender, packet id) receipt,"
        " gateway's own packets excluded",
        rows,
        ("candidate set", "matches", "receipts", "share"),
    )


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
        ("packets with an empty neighbour list", str(sum(1 for r in reports if not r[2]))),
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
        ("gateways positioned inside", str(len(gateways))),
        ("of which usable", str(len(gateways & positions.usable_nodes))),
        ("distinct senders heard by those gateways", str(len(heard))),
        ("relayed receipts at those gateways", str(receipts)),
        ("NeighborInfo reporters positioned inside", str(len(reporters))),
    ]
    print_table(f"Counts inside {area.name}", rows, ("measure", "value"))
    header = ("bits", "packets", "share", "half-width N-S m", "half-width E-W m")
    print_table(
        f"(c) precision_bits on Position packets inside {area.name}",
        precision_rows(positions.bits),
        header,
    )


def report_yield(tally: Tally, area: Area, results: dict[str, RuleResult]) -> float:
    """Measurement (e): link-hours per week per rule; returns the combined 3 km weekly figure."""
    scale = HOURS_PER_WEEK / tally.hours()
    rows = []
    combined_3km = 0.0
    for rule in RULES:
        cells: list[str] = [rule]
        for minimum in (MIN_DISTANCE_M, VALIDATION_DISTANCE_M):
            keys = results[rule].link_hours(minimum)
            per_week = len(keys) * scale
            if rule == "combined" and minimum == VALIDATION_DISTANCE_M:
                combined_3km = per_week
            cells += [
                str(len(keys)),
                f"{per_week:.0f}",
                str(len({k[0] for k in keys})),
                str(len({k[1] for k in keys})),
            ]
        rows.append(tuple(cells))
    header = (
        "rule",
        "500 m observed",
        "500 m per week",
        "500 m relayers",
        "500 m gateways",
        "3 km observed",
        "3 km per week",
        "3 km relayers",
        "3 km gateways",
    )
    title = (
        f"(e) Link-hours inside {area.name}, {tally.hours():.2f} h observed,"
        f" extrapolated x{scale:.2f} to a week"
    )
    print_table(title, rows, header)
    funnel_rows = [
        (step, *[str(results[r].funnel[step]) for r in RULES])
        for step in sorted(results["combined"].funnel)
    ]
    print_table(f"(e) Funnel inside {area.name}, receipts", funnel_rows, ("step", *RULES))
    return combined_3km


def report_sensitivity(tally: Tally, area: Area, positions: AreaPositions) -> None:
    """Combined-rule yield if lnk_02 and lnk_04 were relaxed; diagnostic only, not the contract."""
    scale = HOURS_PER_WEEK / tally.hours()
    rows = []
    for window_s, window_label in ((POSITION_WINDOW_S, "30 min (contract)"), (86_400, "24 h")):
        combined = apply_rules(tally, positions, window_s)["combined"]
        for min_packets in (MIN_PACKETS_PER_HOUR, 1):
            cells = [window_label, str(min_packets)]
            for minimum in (MIN_DISTANCE_M, VALIDATION_DISTANCE_M):
                keys = combined.link_hours(minimum, min_packets)
                cells += [
                    f"{len(keys) * scale:.0f}",
                    str(len({k[0] for k in keys})),
                    str(len({k[1] for k in keys})),
                ]
            rows.append(tuple(cells))
    header = (
        "position window",
        "min packets",
        "500 m per week",
        "500 m relayers",
        "500 m gateways",
        "3 km per week",
        "3 km relayers",
        "3 km gateways",
    )
    title = f"Sensitivity, combined rule inside {area.name}, extrapolated (not contract rules)"
    print_table(title, rows, header)


def report_area(tally: Tally, area: Area) -> float:
    positions = area_positions(tally, area)
    report_area_counts(tally, area, positions)
    combined_3km = report_yield(tally, area, apply_rules(tally, positions))
    report_sensitivity(tally, area, positions)
    return combined_3km


def main() -> None:
    data_dir = Path(sys.argv[1])
    tally = load_capture(data_dir)
    if not tally.counts:
        print("no capture records found")
        return
    report_overview(tally)
    report_relay(tally)
    report_neighbor(tally)
    report_precision(tally)
    report_regional_topics(tally)
    if len(sys.argv) > 2:
        msa = Area("the MSA (CBSA 41180)", load_area(Path(sys.argv[2]), {"41180"}))
        combined_3km = report_area(tally, msa)
        triggered = combined_3km < WIDEN_THRESHOLD_PER_WEEK
        print(
            f"\n(f) Widening check: combined rule at 3 km gives {combined_3km:.0f}"
            f" link-hours per week "
            f"against a threshold of {WIDEN_THRESHOLD_PER_WEEK}; triggered: {triggered}"
        )
        if len(sys.argv) > 3:
            states = Area("Missouri plus Illinois", load_area(Path(sys.argv[3]), {"29", "17"}))
            report_area(tally, states)


if __name__ == "__main__":
    main()
