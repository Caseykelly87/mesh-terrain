"""Decode and measure a spike capture for ADR 0003.

Reads every capture-*.jsonl file in a directory, parses each payload as a
ServiceEnvelope, decrypts default-key packets, decodes Position, NodeInfo,
Telemetry, NeighborInfo and MapReport by portnum, and prints the four
measurements the Phase 1 spike asks for plus the MSA counts and a first
link-hour estimate under the contract rules. Output is aggregate only: no
node id, node name or coordinate is printed. It is not the production decode
asset and keeps nothing on disk.
"""

from __future__ import annotations

import base64
import csv
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
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
GEOD = Geod(ellps="WGS84")
csv.field_size_limit(sys.maxsize)


REGIONAL_SEGMENTS = ("MeshSTL", "MO", "IL")


@dataclass
class Packet:
    received_at: datetime
    topic_kind: str
    region: str
    gateway: str
    channel: str
    sender: int
    packet_id: int
    relay_node: int
    hop_limit: int
    hop_start: int
    via_mqtt: bool
    rx_snr: float
    rx_rssi: int
    status: str
    reason: str = ""
    ok_to_mqtt: bool | None = None
    portnum: int | None = None
    position: tuple[int, int, int] | None = None
    neighbors: list[tuple[int, float]] = field(default_factory=list)
    map_report: tuple[int, int, int] | None = None


@dataclass
class Capture:
    packets: list[Packet] = field(default_factory=list)
    status_counts: Counter[str] = field(default_factory=Counter)
    reason_counts: Counter[str] = field(default_factory=Counter)
    topic_kinds: Counter[str] = field(default_factory=Counter)
    first_at: datetime | None = None
    last_at: datetime | None = None

    def hours(self) -> float:
        if self.first_at is None or self.last_at is None:
            return 0.0
        return max((self.last_at - self.first_at).total_seconds() / 3600.0, 1e-9)


def topic_kind(topic: str) -> str:
    """Classify a topic by its protocol segment: e (protobuf), json, map, stat, other."""
    parts = topic.split("/")
    for part in parts[2:]:
        if part in {"e", "c", "json", "map", "stat"}:
            return "e" if part == "c" else part
    return "other"


def topic_region(topic: str) -> str:
    """The regional segment after msh/US when it is one we treat as St. Louis, else empty."""
    parts = topic.split("/")
    return parts[2] if len(parts) > 2 and parts[2] in REGIONAL_SEGMENTS else ""


def nonce(packet_id: int, sender: int) -> bytes:
    return packet_id.to_bytes(8, "little") + sender.to_bytes(4, "little") + b"\x00" * 4


def decrypt_default(ciphertext: bytes, packet_id: int, sender: int) -> bytes:
    cipher = Cipher(algorithms.AES(DEFAULT_KEY), modes.CTR(nonce(packet_id, sender)))
    decryptor = cipher.decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


def decode_data(packet: Packet, data: mesh_pb2.Data) -> None:
    """Fill the portnum-specific fields of packet from a parsed Data message."""
    packet.portnum = data.portnum
    if data.portnum == portnums_pb2.POSITION_APP:
        position = mesh_pb2.Position()
        position.ParseFromString(data.payload)
        if position.latitude_i or position.longitude_i:
            packet.position = (position.latitude_i, position.longitude_i, position.precision_bits)
    elif data.portnum == portnums_pb2.NEIGHBORINFO_APP:
        info = mesh_pb2.NeighborInfo()
        info.ParseFromString(data.payload)
        packet.neighbors = [(neighbor.node_id, neighbor.snr) for neighbor in info.neighbors]
    elif data.portnum == portnums_pb2.NODEINFO_APP:
        mesh_pb2.User().ParseFromString(data.payload)
    elif data.portnum == portnums_pb2.TELEMETRY_APP:
        telemetry_pb2.Telemetry().ParseFromString(data.payload)
    elif data.portnum == portnums_pb2.MAP_REPORT_APP:
        report = mqtt_pb2.MapReport()
        report.ParseFromString(data.payload)
        packet.map_report = (report.latitude_i, report.longitude_i, report.position_precision)
    if data.portnum not in KNOWN_PORTS:
        packet.status = "unknown_port"


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
    if not mesh_packet.id or not getattr(mesh_packet, "from"):
        return None, "zero_id_or_from"
    try:
        plaintext = decrypt_default(mesh_packet.encrypted, mesh_packet.id, getattr(mesh_packet, "from"))
        data = mesh_pb2.Data()
        data.ParseFromString(plaintext)
    except DecodeError:
        return None, "wrong_key"
    # A wrong key yields noise that usually fails to parse; when it parses, the
    # portnum is almost never one a real node sends.
    if data.portnum == 0 or data.portnum > portnums_pb2.MAX:
        return None, "wrong_key"
    return data, "default_key"


def parse_record(record: dict[str, str]) -> Packet | None:
    """Parse one capture record into a Packet; None when the topic carries no envelope."""
    kind = topic_kind(record["topic"])
    if kind not in {"e", "map"}:
        return None
    received_at = datetime.fromisoformat(record["received_at"])
    envelope = mqtt_pb2.ServiceEnvelope()
    try:
        envelope.ParseFromString(base64.b64decode(record["payload_b64"]))
    except DecodeError:
        return Packet(received_at, kind, topic_region(record["topic"]), "", "", 0, 0, 0, 0, 0, False, 0.0, 0, "malformed")
    mesh_packet = envelope.packet
    packet = Packet(
        received_at=received_at,
        topic_kind=kind,
        region=topic_region(record["topic"]),
        gateway=envelope.gateway_id,
        channel=envelope.channel_id,
        sender=getattr(mesh_packet, "from"),
        packet_id=mesh_packet.id,
        relay_node=mesh_packet.relay_node,
        hop_limit=mesh_packet.hop_limit,
        hop_start=mesh_packet.hop_start,
        via_mqtt=mesh_packet.via_mqtt,
        rx_snr=mesh_packet.rx_snr,
        rx_rssi=mesh_packet.rx_rssi,
        status="ok",
    )
    data, packet.reason = payload_data(mesh_packet)
    if data is None:
        packet.status = "decrypt_failed"
        return packet
    packet.ok_to_mqtt = bool(data.bitfield & 1)
    try:
        decode_data(packet, data)
    except DecodeError:
        packet.status = "malformed"
    return packet


def load_capture(data_dir: Path) -> Capture:
    capture = Capture()
    for path in sorted(data_dir.glob("capture-*.jsonl")):
        with path.open(encoding="ascii") as handle:
            for line in handle:
                record = json.loads(line)
                received_at = datetime.fromisoformat(record["received_at"])
                capture.first_at = capture.first_at or received_at
                capture.last_at = received_at
                capture.topic_kinds[topic_kind(record["topic"])] += 1
                packet = parse_record(record)
                if packet is None:
                    continue
                capture.status_counts[packet.status] += 1
                capture.reason_counts[f"{packet.status}: {packet.reason or 'envelope'}"] += 1
                capture.packets.append(packet)
    return capture


def box_half_width_m(precision_bits: int, latitude_deg: float) -> tuple[float, float]:
    """Half-width of the position box in metres, north-south and east-west.

    A position at N bits keeps the top N bits of the 32-bit integer degrees
    (1e-7 degree units), so the box spans 2 ** (32 - N) units; half of that
    is 2 ** (31 - N) units, or 2 ** (31 - N) * 1e-7 degrees.
    """
    half_deg = 2 ** (31 - precision_bits) * 1e-7
    metres_per_deg_lat = 111_320.0
    return half_deg * metres_per_deg_lat, half_deg * metres_per_deg_lat * math.cos(math.radians(latitude_deg))


def node_id_from_gateway(gateway: str) -> int | None:
    if gateway.startswith("!"):
        try:
            return int(gateway[1:], 16)
        except ValueError:
            return None
    return None


def load_msa(csv_path: Path) -> shapely.Geometry:
    with csv_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("geoid") == "41180":
                return shapely.from_wkt(row["geometry_wkt"])
    raise ValueError(f"no row with geoid 41180 in {csv_path}")


def lonlat(position: tuple[int, int, int]) -> tuple[float, float]:
    return position[1] / 1e7, position[0] / 1e7


def print_table(title: str, rows: list[tuple[str, ...]], header: tuple[str, ...]) -> None:
    print(f"\n## {title}\n")
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join(" --- " for _ in header) + "|")
    for row in rows:
        print("| " + " | ".join(str(cell) for cell in row) + " |")


def pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:.1f}%" if whole else "n/a"


def report_overview(capture: Capture) -> None:
    hours = capture.hours()
    print(f"Capture window: {capture.first_at:%Y-%m-%dT%H:%M}Z to {capture.last_at:%Y-%m-%dT%H:%M}Z ({hours:.2f} h)")
    print_table(
        "Records by topic kind",
        [(kind, str(count), f"{count / hours:.0f}") for kind, count in capture.topic_kinds.most_common()],
        ("kind", "records", "per hour"),
    )
    total = sum(capture.status_counts.values())
    print_table(
        "Decode status (ing_03 set), envelope topics only",
        [(status, str(count), pct(count, total)) for status, count in capture.status_counts.most_common()],
        ("status", "packets", "share"),
    )
    print_table(
        "Decode status by reason",
        [(reason, str(count), pct(count, total)) for reason, count in capture.reason_counts.most_common()],
        ("status: reason", "packets", "share"),
    )
    consent = Counter(p.ok_to_mqtt for p in capture.packets if p.ok_to_mqtt is not None)
    print_table(
        "OK_TO_MQTT bit on packets that arrived with a payload",
        [(str(flag), str(count), pct(count, sum(consent.values()))) for flag, count in consent.most_common()],
        ("ok_to_mqtt", "packets", "share"),
    )
    senders: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for packet in capture.packets:
        if packet.sender and packet.topic_kind == "e" and packet.status != "malformed":
            senders[packet.sender][0 if packet.reason == "stripped" else 1] += 1
    mix = Counter("both" if s and f else "stripped only" if s else "payload only" for s, f in senders.values())
    print_table(
        "Senders by whether their packets arrive stripped, with a payload, or both",
        [(kind, str(count), pct(count, len(senders))) for kind, count in mix.most_common()],
        ("sender kind", "senders", "share"),
    )
    ports = Counter(p.portnum for p in capture.packets if p.portnum is not None)
    rows = [(portnums_pb2.PortNum.Name(port) if port in portnums_pb2.PortNum.values() else str(port), str(n)) for port, n in ports.most_common(12)]
    print_table("Decoded portnums (top 12)", rows, ("portnum", "packets"))


def relay_candidates(packets: list[Packet]) -> tuple[dict[int, set[int]], dict[str, dict[int, set[int]]]]:
    """Low id byte to node ids, over the whole capture and per gateway."""
    everywhere: dict[int, set[int]] = defaultdict(set)
    per_gateway: dict[str, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
    for packet in packets:
        if packet.sender:
            everywhere[packet.sender & 0xFF].add(packet.sender)
            per_gateway[packet.gateway][packet.sender & 0xFF].add(packet.sender)
        for neighbor, _ in packet.neighbors:
            everywhere[neighbor & 0xFF].add(neighbor)
    return everywhere, per_gateway


def report_relay(capture: Capture) -> dict[str, int]:
    """Measurement (a): relay_node presence and how it resolves."""
    headers = [p for p in capture.packets if p.topic_kind == "e" and p.status != "malformed" and p.sender]
    with_relay = [p for p in headers if p.relay_node]
    self_relay = [p for p in with_relay if p.relay_node == (p.sender & 0xFF)]
    own_uplink = [p for p in headers if node_id_from_gateway(p.gateway) == p.sender]
    everywhere, per_gateway = relay_candidates(headers)
    resolution = {"capture": Counter(), "gateway": Counter()}
    for packet in with_relay:
        if node_id_from_gateway(packet.gateway) == packet.sender:
            continue
        for scope, table in (("capture", everywhere), ("gateway", per_gateway[packet.gateway])):
            count = len(table.get(packet.relay_node, ()))
            resolution[scope]["one" if count == 1 else "many" if count > 1 else "none"] += 1
    rows = [
        ("packets with a parsed header (sender set)", str(len(headers)), "100%"),
        ("gateway's own packets (sender is the gateway)", str(len(own_uplink)), pct(len(own_uplink), len(headers))),
        ("relay_node non-zero", str(len(with_relay)), pct(len(with_relay), len(headers))),
        ("relay_node equals the sender's low byte (heard direct from origin)", str(len(self_relay)), pct(len(self_relay), len(with_relay))),
        ("hop_limit is zero", str(sum(1 for p in headers if p.hop_limit == 0)), pct(sum(1 for p in headers if p.hop_limit == 0), len(headers))),
        ("hop_start non-zero", str(sum(1 for p in headers if p.hop_start)), pct(sum(1 for p in headers if p.hop_start), len(headers))),
        ("via_mqtt set", str(sum(1 for p in headers if p.via_mqtt)), pct(sum(1 for p in headers if p.via_mqtt), len(headers))),
    ]
    print_table("(a) relay_node on packets from the e topics", rows, ("measure", "packets", "share"))
    rows = []
    for scope, label in (("capture", "all senders in the capture"), ("gateway", "senders heard by the same gateway")):
        total = sum(resolution[scope].values())
        for outcome in ("one", "many", "none"):
            rows.append((label, outcome, str(resolution[scope][outcome]), pct(resolution[scope][outcome], total)))
    print_table("(a) relay_node low byte resolution, excluding the gateway's own packets", rows, ("candidate set", "matches", "packets", "share"))
    return {"headers": len(headers), "with_relay": len(with_relay)}


def report_neighbor(capture: Capture) -> None:
    """Measurement (b): NeighborInfo volume and distinct pairs."""
    reports = [p for p in capture.packets if p.portnum == portnums_pb2.NEIGHBORINFO_APP]
    pairs = {(neighbor, p.sender) for p in reports for neighbor, _ in p.neighbors}
    snrs = [snr for p in reports for _, snr in p.neighbors]
    rows = [
        ("NeighborInfo packets", str(len(reports))),
        ("packets per hour", f"{len(reports) / capture.hours():.1f}"),
        ("distinct reporters", str(len({p.sender for p in reports}))),
        ("distinct (neighbour, reporter) pairs", str(len(pairs))),
        ("neighbour entries", str(len(snrs))),
        ("median SNR reported (dB)", f"{statistics.median(snrs):.1f}" if snrs else "n/a"),
        ("packets with an empty neighbour list", str(sum(1 for p in reports if not p.neighbors))),
    ]
    print_table("(b) NeighborInfo", rows, ("measure", "value"))


def report_precision(capture: Capture) -> None:
    """Measurement (c): precision_bits distribution on Position packets."""
    bits = Counter(p.position[2] for p in capture.packets if p.position)
    total = sum(bits.values())
    rows = []
    for value in sorted(bits):
        ns, ew = box_half_width_m(value, ST_LOUIS_LATITUDE_DEG) if 0 < value <= 32 else (0.0, 0.0)
        rows.append((str(value), str(bits[value]), pct(bits[value], total), f"{ns:.0f}", f"{ew:.0f}"))
    print_table("(c) precision_bits on Position packets (half-widths at St. Louis latitude)", rows, ("precision_bits", "packets", "share", "half-width N-S m", "half-width E-W m"))
    map_bits = Counter(p.map_report[2] for p in capture.packets if p.map_report)
    rows = [(str(v), str(map_bits[v]), pct(map_bits[v], sum(map_bits.values()))) for v in sorted(map_bits)]
    print_table("(c) position_precision on MapReport packets (map topic)", rows, ("position_precision", "packets", "share"))


@dataclass
class Positioned:
    """Positions per node as (received_at, lon, lat, bits), sorted by time."""

    by_node: dict[int, list[tuple[datetime, float, float, int]]] = field(default_factory=dict)

    def add(self, node: int, at: datetime, lon: float, lat: float, bits: int) -> None:
        self.by_node.setdefault(node, []).append((at, lon, lat, bits))

    def near(self, node: int, at: datetime, min_bits: int) -> tuple[float, float] | None:
        best: tuple[float, tuple[float, float]] | None = None
        for seen_at, lon, lat, bits in self.by_node.get(node, ()):
            gap = abs((seen_at - at).total_seconds())
            if bits >= min_bits and gap <= POSITION_WINDOW_S and (best is None or gap < best[0]):
                best = (gap, (lon, lat))
        return None if best is None else best[1]


def collect_positions(capture: Capture, inside: shapely.Geometry) -> tuple[Positioned, set[int], set[str]]:
    """Positions of nodes inside the MSA, plus the set of MSA nodes and MSA gateways."""
    prepared = prep(inside)
    positioned = Positioned()
    msa_nodes: set[int] = set()
    for packet in capture.packets:
        if packet.position and packet.sender:
            lon, lat = lonlat(packet.position)
            if prepared.contains(shapely.Point(lon, lat)):
                positioned.add(packet.sender, packet.received_at, lon, lat, packet.position[2])
                msa_nodes.add(packet.sender)
        if packet.map_report and packet.sender:
            lon, lat = lonlat(packet.map_report)
            if prepared.contains(shapely.Point(lon, lat)):
                positioned.add(packet.sender, packet.received_at, lon, lat, packet.map_report[2])
                msa_nodes.add(packet.sender)
    msa_gateways = {p.gateway for p in capture.packets if node_id_from_gateway(p.gateway) in msa_nodes}
    return positioned, msa_nodes, msa_gateways


def resolve_relayer(packet: Packet, candidates: dict[int, set[int]]) -> int | None:
    matches = candidates.get(packet.relay_node, set())
    return next(iter(matches)) if len(matches) == 1 else None


def link_hours_relay(capture: Capture, positioned: Positioned, msa_gateways: set[str], min_bits: int) -> dict[str, int]:
    """Link-hours from relay_node under lnk_01 to lnk_05, relayer resolved among senders the gateway heard."""
    headers = [p for p in capture.packets if p.topic_kind == "e" and p.sender and p.gateway in msa_gateways]
    _, per_gateway = relay_candidates(headers)
    windows: dict[tuple[int, int, str], list[float]] = defaultdict(list)
    receipts = 0
    for packet in headers:
        gateway_node = node_id_from_gateway(packet.gateway)
        if gateway_node is None or packet.sender == gateway_node or not packet.relay_node:
            continue
        relayer = resolve_relayer(packet, per_gateway[packet.gateway])
        if relayer is None or relayer == gateway_node:
            continue
        relayer_at = positioned.near(relayer, packet.received_at, min_bits)
        gateway_at = positioned.near(gateway_node, packet.received_at, min_bits)
        if relayer_at is None or gateway_at is None:
            continue
        distance_m = GEOD.inv(relayer_at[0], relayer_at[1], gateway_at[0], gateway_at[1])[2]
        if not MIN_DISTANCE_M <= distance_m <= MAX_DISTANCE_M:
            continue
        receipts += 1
        windows[(relayer, gateway_node, packet.received_at.strftime("%Y-%m-%dT%H"))].append(distance_m)
    kept = {key: d for key, d in windows.items() if len(d) >= MIN_PACKETS_PER_HOUR}
    return {
        "receipts": receipts,
        "link_hours": len(kept),
        "link_hours_over_3km": sum(1 for d in kept.values() if d[0] >= VALIDATION_DISTANCE_M),
        "distinct_pairs": len({(r, g) for r, g, _ in kept}),
    }


def link_hours_neighbor(capture: Capture, positioned: Positioned, min_bits: int) -> dict[str, int]:
    """Link-hours from NeighborInfo pairs: neighbour heard by reporter, both positioned inside the MSA."""
    windows: dict[tuple[int, int, str], list[float]] = defaultdict(list)
    for packet in capture.packets:
        if packet.portnum != portnums_pb2.NEIGHBORINFO_APP:
            continue
        reporter_at = positioned.near(packet.sender, packet.received_at, min_bits)
        if reporter_at is None:
            continue
        for neighbor, _ in packet.neighbors:
            if neighbor == packet.sender:
                continue
            neighbor_at = positioned.near(neighbor, packet.received_at, min_bits)
            if neighbor_at is None:
                continue
            distance_m = GEOD.inv(neighbor_at[0], neighbor_at[1], reporter_at[0], reporter_at[1])[2]
            if MIN_DISTANCE_M <= distance_m <= MAX_DISTANCE_M:
                windows[(neighbor, packet.sender, packet.received_at.strftime("%Y-%m-%dT%H"))].append(distance_m)
    return {
        "pair_hours_any": len(windows),
        "pair_hours_over_3km": sum(1 for d in windows.values() if d[0] >= VALIDATION_DISTANCE_M),
        "pair_hours_3_reports": sum(1 for d in windows.values() if len(d) >= MIN_PACKETS_PER_HOUR),
        "distinct_pairs": len({(n, r) for n, r, _ in windows}),
    }


def report_regional_topics(capture: Capture) -> None:
    """Traffic under the St. Louis, Missouri and Illinois topic segments of the public broker."""
    hours = capture.hours()
    rows = []
    for segment in REGIONAL_SEGMENTS:
        packets = [p for p in capture.packets if p.region == segment and p.topic_kind == "e"]
        relayed = sum(1 for p in packets if p.relay_node and node_id_from_gateway(p.gateway) != p.sender)
        with_payload = sum(1 for p in packets if p.status == "ok")
        positions = sum(1 for p in packets if p.position)
        rows.append((
            f"msh/US/{segment}",
            str(len(packets)),
            f"{len(packets) / hours:.0f}",
            str(len({p.gateway for p in packets})),
            str(len({p.sender for p in packets if p.sender})),
            pct(relayed, len(packets)),
            pct(with_payload, len(packets)),
            str(positions),
        ))
    print_table(
        "Regional topic segments on the public broker (e topics)",
        rows,
        ("topic root", "packets", "per hour", "gateways", "senders", "relay_node set", "with payload", "positions"),
    )


def report_msa(capture: Capture, msa: shapely.Geometry) -> None:
    positioned, msa_nodes, msa_gateways = collect_positions(capture, msa)
    heard_by_msa = {p.sender for p in capture.packets if p.gateway in msa_gateways and p.sender}
    usable_nodes = {n for n, rows in positioned.by_node.items() if any(b >= USABLE_BITS for _, _, _, b in rows)}
    coarse_nodes = {n for n, rows in positioned.by_node.items() if any(b >= COARSE_BITS for _, _, _, b in rows)}
    gateway_nodes = {node_id_from_gateway(g) for g in msa_gateways}
    rows = [
        ("nodes with a position inside the MSA (any precision)", str(len(msa_nodes))),
        ("of which usable (precision_bits >= 15)", str(len(usable_nodes))),
        ("of which coarse or better (precision_bits >= 12)", str(len(coarse_nodes))),
        ("gateways whose own position is inside the MSA", str(len(msa_gateways))),
        ("of which with a usable position", str(len(gateway_nodes & usable_nodes))),
        ("distinct senders heard by those gateways", str(len(heard_by_msa))),
        ("packets received by those gateways", str(sum(1 for p in capture.packets if p.gateway in msa_gateways))),
        ("NeighborInfo packets from MSA nodes", str(sum(1 for p in capture.packets if p.portnum == portnums_pb2.NEIGHBORINFO_APP and p.sender in msa_nodes))),
    ]
    print_table("MSA counts (CBSA 41180)", rows, ("measure", "value"))
    hours = capture.hours()
    rows = []
    for min_bits, label in ((USABLE_BITS, "usable, bits >= 15"), (COARSE_BITS, "coarse or better, bits >= 12")):
        relay = link_hours_relay(capture, positioned, msa_gateways, min_bits)
        neighbor = link_hours_neighbor(capture, positioned, min_bits)
        rows.append((label, "relay_node", str(relay["receipts"]), str(relay["link_hours"]), str(relay["link_hours_over_3km"]), str(relay["distinct_pairs"]), f"{relay['link_hours'] / hours * 168:.0f}"))
        rows.append((label, "NeighborInfo (any report)", "n/a", str(neighbor["pair_hours_any"]), str(neighbor["pair_hours_over_3km"]), str(neighbor["distinct_pairs"]), f"{neighbor['pair_hours_any'] / hours * 168:.0f}"))
        rows.append((label, "NeighborInfo (3 reports)", "n/a", str(neighbor["pair_hours_3_reports"]), "n/a", "n/a", f"{neighbor['pair_hours_3_reports'] / hours * 168:.0f}"))
    print_table(
        "Link-hours inside the MSA under lnk_01 to lnk_05 (500 m to 60 km, 30 min position window)",
        rows,
        ("positions", "link source", "receipts", "link-hours", "over 3 km", "distinct pairs", "per week"),
    )


def main() -> None:
    data_dir = Path(sys.argv[1])
    msa_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    capture = load_capture(data_dir)
    if not capture.packets:
        print("no envelope packets found")
        return
    report_overview(capture)
    report_relay(capture)
    report_neighbor(capture)
    report_precision(capture)
    report_regional_topics(capture)
    if msa_path is not None:
        report_msa(capture, load_msa(msa_path))


if __name__ == "__main__":
    main()
