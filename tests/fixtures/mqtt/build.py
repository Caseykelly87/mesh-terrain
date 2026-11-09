"""Build the synthetic MQTT deliveries in deliveries.jsonl (ADR 0005, decoder fixtures).

Every delivery is made here from invented values: node ids in the reserved
range 0xF1C70000 to 0xF1C7FFFF, invented text, invented positions in the open
South Atlantic, nothing read from any capture. Encrypted packets use the
documented default channel key with the firmware's nonce (packet id as 8 bytes
little-endian, sender id as 4 bytes little-endian, 4 zero bytes). Output is
deterministic: rebuilding gives the same bytes, and a test checks the
committed file against a rebuild.

Run from the repository root: `uv run python tests/fixtures/mqtt/build.py`.
"""

import base64
import json
from collections.abc import Callable
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from meshtastic.protobuf import (
    config_pb2,
    mesh_pb2,
    mqtt_pb2,
    portnums_pb2,
    telemetry_pb2,
)

OUTPUT = Path(__file__).with_name("deliveries.jsonl")
DEFAULT_KEY = base64.b64decode("1PG7OiApB1nwvP+rz05pAQ==")
OTHER_KEY = bytes(range(16))
ID_PREFIX = 0xF1C70000
GATEWAY = ID_PREFIX | 0x0001
GATEWAY_TWO = ID_PREFIX | 0x0002
SENDER = ID_PREFIX | 0x0011
RELAYER = ID_PREFIX | 0x0022
NEIGHBOUR = ID_PREFIX | 0x0033
INVENTED_TEXT = "invented fixture text, kestrel over the quarry"
INVENTED_NAME = "Invented Fixture Node"
INVENTED_SHORT = "IFN1"
INVENTED_PUBLIC_KEY = bytes(range(32))
# Open South Atlantic, far from any St. Louis box and from any spike position.
LATITUDE_I = -301234567
LONGITUDE_I = -251234567
ROOT = "msh/US/Example"
HOUR = "2026-01-01T07"


def node_text(node_id: int) -> str:
    return f"!{node_id:08x}"


def e_topic(channel: str = "LongFast", gateway: int = GATEWAY) -> str:
    return f"{ROOT}/2/e/{channel}/{node_text(gateway)}"


def encrypt(plaintext: bytes, packet_id: int, sender: int, key: bytes = DEFAULT_KEY) -> bytes:
    nonce = packet_id.to_bytes(8, "little") + sender.to_bytes(4, "little") + b"\x00" * 4
    encryptor = Cipher(algorithms.AES(key), modes.CTR(nonce)).encryptor()
    return encryptor.update(plaintext) + encryptor.finalize()


def packet(packet_id: int, sender: int = SENDER, **header: object) -> mesh_pb2.MeshPacket:
    mesh_packet = mesh_pb2.MeshPacket(id=packet_id, to=0xFFFFFFFF, channel=8, hop_limit=3)
    setattr(mesh_packet, "from", sender)
    mesh_packet.rx_time = 1767250800
    mesh_packet.rx_snr = 6.25
    mesh_packet.rx_rssi = -97
    mesh_packet.hop_start = 3
    mesh_packet.relay_node = RELAYER & 0xFF
    for name, value in header.items():
        setattr(mesh_packet, name, value)
    return mesh_packet


def envelope(mesh_packet: mesh_pb2.MeshPacket, gateway: int = GATEWAY) -> bytes:
    service = mqtt_pb2.ServiceEnvelope(channel_id="LongFast", gateway_id=node_text(gateway))
    service.packet.CopyFrom(mesh_packet)
    return service.SerializeToString()


def encrypted(portnum: int, payload: bytes, packet_id: int, **header: object) -> bytes:
    data = mesh_pb2.Data(portnum=portnum, payload=payload)
    mesh_packet = packet(packet_id, **header)
    sender = getattr(mesh_packet, "from")
    mesh_packet.encrypted = encrypt(data.SerializeToString(), packet_id, sender)
    return envelope(mesh_packet)


def plaintext(portnum: int, payload: bytes, packet_id: int, **header: object) -> bytes:
    mesh_packet = packet(packet_id, **header)
    mesh_packet.decoded.portnum = portnum
    mesh_packet.decoded.payload = payload
    return envelope(mesh_packet)


def position() -> bytes:
    message = mesh_pb2.Position(
        latitude_i=LATITUDE_I,
        longitude_i=LONGITUDE_I,
        altitude=123,
        time=1767250700,
        location_source=mesh_pb2.Position.LOC_INTERNAL,
        altitude_source=mesh_pb2.Position.ALT_INTERNAL,
        precision_bits=16,
    )
    return message.SerializeToString()


def map_report() -> bytes:
    message = mqtt_pb2.MapReport(
        long_name=INVENTED_NAME,
        short_name=INVENTED_SHORT,
        role=config_pb2.Config.DeviceConfig.ROUTER,
        hw_model=mesh_pb2.HardwareModel.HELTEC_V3,
        firmware_version="0.0.0.invented",
        region=config_pb2.Config.LoRaConfig.US,
        modem_preset=config_pb2.Config.LoRaConfig.LONG_FAST,
        latitude_i=LATITUDE_I + 1000,
        longitude_i=LONGITUDE_I + 1000,
        altitude=45,
        position_precision=13,
        num_online_local_nodes=7,
    )
    return message.SerializeToString()


def node_info() -> bytes:
    message = mesh_pb2.User(
        id=node_text(SENDER),
        long_name=INVENTED_NAME,
        short_name=INVENTED_SHORT,
        macaddr=bytes(6),
        hw_model=mesh_pb2.HardwareModel.RAK4631,
        role=config_pb2.Config.DeviceConfig.CLIENT,
        public_key=INVENTED_PUBLIC_KEY,
    )
    return message.SerializeToString()


def neighbor_info() -> bytes:
    message = mesh_pb2.NeighborInfo(node_id=SENDER, last_sent_by_id=SENDER)
    message.neighbors.add(node_id=NEIGHBOUR, snr=4.5, last_rx_time=1767250600)
    message.neighbors.add(node_id=GATEWAY, snr=-3.25, last_rx_time=1767250650)
    return message.SerializeToString()


def telemetry() -> bytes:
    message = telemetry_pb2.Telemetry(time=1767250750)
    metrics = message.device_metrics
    metrics.battery_level = 87
    metrics.voltage = 4.01
    metrics.channel_utilization = 12.5
    metrics.air_util_tx = 1.75
    metrics.uptime_seconds = 86400
    return message.SerializeToString()


def wrong_key() -> bytes:
    mesh_packet = packet(0x1009)
    data = mesh_pb2.Data(portnum=portnums_pb2.TEXT_MESSAGE_APP, payload=INVENTED_TEXT.encode())
    mesh_packet.encrypted = encrypt(data.SerializeToString(), 0x1009, SENDER, OTHER_KEY)
    return envelope(mesh_packet)


def stripped(packet_id: int) -> bytes:
    return envelope(packet(packet_id))


def map_topic_report() -> bytes:
    mesh_packet = packet(0x1015, hop_start=0, relay_node=0, rx_snr=0.0, rx_rssi=0, rx_time=0)
    mesh_packet.decoded.portnum = portnums_pb2.MAP_REPORT_APP
    mesh_packet.decoded.payload = map_report()
    return envelope(mesh_packet)


Builder = Callable[[], bytes]
TEXT = portnums_pb2.TEXT_MESSAGE_APP
# (name, broker, received_at, topic, builder)
DELIVERIES: list[tuple[str, str, str, str, Builder]] = [
    (
        "position",
        "public",
        f"{HOUR}:00:01.000001+00:00",
        e_topic(),
        lambda: encrypted(portnums_pb2.POSITION_APP, position(), 0x1001),
    ),
    (
        "mapreport_e",
        "public",
        f"{HOUR}:00:02.000000+00:00",
        e_topic(),
        lambda: encrypted(portnums_pb2.MAP_REPORT_APP, map_report(), 0x1002),
    ),
    ("mapreport_map", "public", f"{HOUR}:00:03.000000+00:00", f"{ROOT}/2/map/", map_topic_report),
    (
        "nodeinfo",
        "public",
        f"{HOUR}:00:04.000000+00:00",
        e_topic(),
        lambda: encrypted(portnums_pb2.NODEINFO_APP, node_info(), 0x1003),
    ),
    (
        "neighborinfo",
        "meshstl",
        f"{HOUR}:00:05.000000+00:00",
        e_topic(),
        lambda: encrypted(portnums_pb2.NEIGHBORINFO_APP, neighbor_info(), 0x1004),
    ),
    (
        "telemetry",
        "public",
        f"{HOUR}:00:06.000000+00:00",
        e_topic(),
        lambda: encrypted(portnums_pb2.TELEMETRY_APP, telemetry(), 0x1005),
    ),
    (
        "text",
        "public",
        f"{HOUR}:00:07.000000+00:00",
        e_topic(),
        lambda: encrypted(TEXT, INVENTED_TEXT.encode(), 0x1006),
    ),
    (
        "unknown_port",
        "public",
        f"{HOUR}:00:08.000000+00:00",
        e_topic(),
        lambda: encrypted(92, b"\x01\x02", 0x1007),
    ),
    ("stripped", "public", f"{HOUR}:00:09.000000+00:00", e_topic(), lambda: stripped(0x1008)),
    ("wrong_key", "meshstl", f"{HOUR}:00:10.000000+00:00", e_topic(), wrong_key),
    (
        "pki",
        "public",
        f"{HOUR}:00:11.000000+00:00",
        e_topic("PKI"),
        lambda: envelope(packet(0x100A, encrypted=b"\x99" * 24, pki_encrypted=True)),
    ),
    (
        "via_mqtt_copy",
        "public",
        f"{HOUR}:00:12.000000+00:00",
        e_topic(gateway=GATEWAY_TWO),
        lambda: envelope(packet(0x1001, via_mqtt=True), gateway=GATEWAY_TWO),
    ),
    (
        "malformed_envelope",
        "public",
        f"{HOUR}:00:13.000000+00:00",
        e_topic(),
        lambda: b"\xff\xff\xff\xff not a protobuf",
    ),
    (
        "portnum_zero",
        "public",
        f"{HOUR}:00:14.000000+00:00",
        e_topic(),
        lambda: plaintext(0, b"\x01", 0x100B),
    ),
    (
        "portnum_negative",
        "public",
        f"{HOUR}:00:15.000000+00:00",
        e_topic(),
        lambda: plaintext(-3, b"\x01", 0x100C),
    ),
    (
        "packet_id_zero",
        "public",
        f"{HOUR}:00:16.000000+00:00",
        e_topic(),
        lambda: encrypted(TEXT, INVENTED_TEXT.encode(), 0),
    ),
    (
        "from_zero",
        "public",
        f"{HOUR}:00:17.000000+00:00",
        e_topic(),
        lambda: encrypted(TEXT, INVENTED_TEXT.encode(), 0x100D, **{"from": 0}),
    ),
    (
        "bad_position_payload",
        "public",
        f"{HOUR}:00:18.000000+00:00",
        e_topic(),
        lambda: plaintext(portnums_pb2.POSITION_APP, b"\xff\xff\xff", 0x100E),
    ),
    (
        "unset_header",
        "public",
        f"{HOUR}:00:19.000000+00:00",
        e_topic("MediumFast"),
        lambda: encrypted(
            portnums_pb2.ROUTING_APP,
            b"",
            0x100F,
            relay_node=0,
            hop_start=0,
            rx_time=0,
            rx_snr=0.0,
            rx_rssi=0,
            channel=0,
        ),
    ),
    (
        "decrypted_portnum_zero",
        "public",
        f"{HOUR}:00:24.000000+00:00",
        e_topic(),
        lambda: encrypted(0, b"", 0x1013),
    ),
    (
        "decrypted_portnum_above_max",
        "public",
        f"{HOUR}:00:25.000000+00:00",
        e_topic(),
        lambda: encrypted(portnums_pb2.MAX + 1, b"", 0x1014),
    ),
    (
        "json",
        "public",
        f"{HOUR}:00:20.000000+00:00",
        f"{ROOT}/2/json/LongFast/{node_text(GATEWAY)}",
        lambda: json.dumps({"from": SENDER, "payload": {"text": INVENTED_TEXT}}).encode(),
    ),
    (
        "stat",
        "public",
        f"{HOUR}:00:21.000000+00:00",
        f"{ROOT}/2/stat/{node_text(GATEWAY)}",
        lambda: b"online",
    ),
    ("other_topic", "public", f"{HOUR}:00:22.000000+00:00", f"{ROOT}/Example", lambda: b"x"),
    (
        "map_unparsed",
        "public",
        f"{HOUR}:00:23.000000+00:00",
        f"{ROOT}/2/map/",
        lambda: b"\xff\xff\xff\xff",
    ),
    (
        "both_brokers",
        "public",
        f"{HOUR}:30:00.000000+00:00",
        e_topic(),
        lambda: encrypted(portnums_pb2.POSITION_APP, position(), 0x1010),
    ),
    (
        "both_brokers",
        "meshstl",
        f"{HOUR}:30:00.500000+00:00",
        e_topic(),
        lambda: encrypted(portnums_pb2.POSITION_APP, position(), 0x1010),
    ),
    (
        "repeat",
        "public",
        f"{HOUR}:40:00.000000+00:00",
        e_topic(),
        lambda: encrypted(TEXT, INVENTED_TEXT.encode(), 0x1011),
    ),
    (
        "repeat",
        "public",
        "2026-01-01T08:00:01.000000+00:00",
        e_topic(),
        lambda: encrypted(TEXT, INVENTED_TEXT.encode(), 0x1011),
    ),
    (
        "stripped_public_whole_meshstl",
        "public",
        f"{HOUR}:50:00.000000+00:00",
        e_topic(),
        lambda: stripped(0x1012),
    ),
    (
        "stripped_public_whole_meshstl",
        "meshstl",
        f"{HOUR}:50:01.000000+00:00",
        e_topic(),
        lambda: encrypted(portnums_pb2.POSITION_APP, position(), 0x1012),
    ),
]


def records() -> list[dict[str, str]]:
    """Every fixture delivery as a capture-shaped record, in file order."""
    return [
        {
            "name": name,
            "broker": broker,
            "received_at": received_at,
            "topic": topic,
            "payload_b64": base64.b64encode(build()).decode("ascii"),
        }
        for name, broker, received_at, topic, build in DELIVERIES
    ]


def render() -> str:
    """The deliveries.jsonl text."""
    return "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records())


if __name__ == "__main__":
    OUTPUT.write_text(render(), encoding="utf-8", newline="\n")
