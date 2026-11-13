"""The synthetic MQTT fixtures are built, never recorded (ADR 0005, prv_01).

The committed deliveries.jsonl must equal a fresh build, so nothing in it can
have come from a capture. Every fixture is opened the way the decoder opens
it (envelope, default-key decryption, the portnum's message) and every node id
found anywhere in it must lie in the invented range and be off the denylist;
every decoded coordinate must lie in the invented South Atlantic box; and the
builder must not read any file.
"""

import ast
import base64
import importlib.util
import json
import re
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from google.protobuf.message import DecodeError
from meshtastic.protobuf import mesh_pb2, mqtt_pb2, portnums_pb2

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "mqtt"
NODE_PATTERN = re.compile(r"!([0-9a-fA-F]{8})")
INVENTED_RANGE = range(0xF1C70000, 0xF1C80000)
DEFAULT_KEY = base64.b64decode("1PG7OiApB1nwvP+rz05pAQ==")
# Latitude and longitude bounds of the invented box, in 1e-7 degrees.
LATITUDE_BOX = (-310000000, -290000000)
LONGITUDE_BOX = (-260000000, -240000000)
# Senders the builder sets on purpose to a value outside the range.
SPECIAL_SENDERS = {"from_zero": 0}
# Fixtures whose payload is not meant to open with the default key.
NOT_DEFAULT_KEY = {"wrong_key", "pki"}
EXPECTED_NAMES = {
    "position",
    "mapreport_e",
    "mapreport_map",
    "nodeinfo",
    "neighborinfo",
    "telemetry",
    "text",
    "unknown_port",
    "stripped",
    "wrong_key",
    "pki",
    "via_mqtt_copy",
    "malformed_envelope",
    "portnum_zero",
    "portnum_negative",
    "packet_id_zero",
    "from_zero",
    "bad_position_payload",
    "unset_header",
    "json",
    "stat",
    "other_topic",
    "map_unparsed",
    "decrypted_portnum_zero",
    "decrypted_portnum_above_max",
    "decrypted_portnum_negative",
    "plaintext_portnum_above_max",
    "snr_zero_with_rssi",
    "uint32_maximum_values",
    "uint32_maximum_precision",
    "custom_gateway_segment",
    "mapreport_zero_coordinates",
    "telemetry_without_device_metrics",
    "both_brokers",
    "repeat",
    "stripped_public_whole_meshstl",
}
# Calls that would read a file, a folder listing or a capture.
READING_CALLS = {
    "open",
    "read_text",
    "read_bytes",
    "load",
    "glob",
    "rglob",
    "iterdir",
    "listdir",
    "scandir",
    "walk",
    "read_table",
    "read_csv",
}


def load_builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fixture_build", FIXTURES / "build.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def committed() -> list[dict[str, str]]:
    text = (FIXTURES / "deliveries.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def denylist() -> set[int]:
    lines = (FIXTURES / "denylist.txt").read_text(encoding="utf-8").splitlines()
    return {int(line[1:], 16) for line in lines if line.startswith("!")}


def envelope(record: dict[str, str]) -> Any:
    service = mqtt_pb2.ServiceEnvelope()
    try:
        service.ParseFromString(base64.b64decode(record["payload_b64"]))
    except DecodeError:
        return None
    return service


def data_of(packet: Any) -> Any:
    """The Data message of a packet, decrypting with the default key; None when there is none."""
    if packet.HasField("decoded"):
        return packet.decoded
    if not packet.encrypted or packet.pki_encrypted:
        return None
    nonce = packet.id.to_bytes(8, "little") + getattr(packet, "from").to_bytes(4, "little")
    decryptor = Cipher(algorithms.AES(DEFAULT_KEY), modes.CTR(nonce + bytes(4))).decryptor()
    data = mesh_pb2.Data()
    data.ParseFromString(decryptor.update(packet.encrypted) + decryptor.finalize())
    return data


def payload_message(data: Any) -> Any:
    """The portnum's message, or None for a port that carries no ids or places."""
    messages = {
        portnums_pb2.POSITION_APP: mesh_pb2.Position,
        portnums_pb2.MAP_REPORT_APP: mqtt_pb2.MapReport,
        portnums_pb2.NODEINFO_APP: mesh_pb2.User,
        portnums_pb2.NEIGHBORINFO_APP: mesh_pb2.NeighborInfo,
    }
    if data.portnum not in messages:
        return None
    message = messages[data.portnum]()
    try:
        message.ParseFromString(data.payload)
    except DecodeError:
        return None
    return message


def opened(record: dict[str, str]) -> tuple[Any, Any]:
    """(envelope, payload message) of a record, each None where it does not open."""
    service = envelope(record)
    if service is None or not service.HasField("packet"):
        return service, None
    if record["name"] in NOT_DEFAULT_KEY:
        return service, None
    data = data_of(service.packet)
    return service, None if data is None else payload_message(data)


def ids_in_text(text: str) -> list[int]:
    return [int(match, 16) for match in NODE_PATTERN.findall(text)]


def payload_ids(message: Any) -> Iterator[int]:
    if isinstance(message, mesh_pb2.NeighborInfo):
        yield message.node_id
        yield message.last_sent_by_id
        yield from (neighbour.node_id for neighbour in message.neighbors)
    if isinstance(message, mesh_pb2.User):
        yield from ids_in_text(message.id)


def node_ids(record: dict[str, str]) -> list[int]:
    """Every node id in the topic, the envelope, the packet header and the decrypted payload."""
    ids = ids_in_text(record["topic"])
    service, message = opened(record)
    if service is not None and service.HasField("packet"):
        ids += ids_in_text(service.gateway_id)
        if record["name"] not in SPECIAL_SENDERS:
            ids.append(getattr(service.packet, "from"))
    if message is not None:
        ids += list(payload_ids(message))
    return ids


def coordinates(record: dict[str, str]) -> list[tuple[int, int]]:
    _, message = opened(record)
    if isinstance(message, mesh_pb2.Position | mqtt_pb2.MapReport):
        return [(message.latitude_i, message.longitude_i)]
    return []


def test_committed_fixtures_equal_a_fresh_build() -> None:
    assert (FIXTURES / "deliveries.jsonl").read_text(encoding="utf-8") == load_builder().render()


def test_every_packet_type_of_the_brief_has_a_fixture() -> None:
    assert {record["name"] for record in committed()} == EXPECTED_NAMES


def test_both_broker_fixtures_have_one_delivery_per_broker() -> None:
    brokers = [record["broker"] for record in committed() if record["name"] == "both_brokers"]
    assert sorted(brokers) == ["meshstl", "public"]
    pair = [record["payload_b64"] for record in committed() if record["name"] == "both_brokers"]
    assert pair[0] == pair[1]


def test_every_node_id_is_invented_and_none_is_denylisted() -> None:
    banned = denylist()
    for record in committed():
        for node_id in node_ids(record):
            assert node_id in INVENTED_RANGE, record["name"]
            assert node_id not in banned, record["name"]


def test_the_payload_ids_are_walked() -> None:
    # The walk must reach inside NeighborInfo and NodeInfo, or the test above proves little.
    by_name = {record["name"]: record for record in committed()}
    assert len(list(payload_ids(opened(by_name["neighborinfo"])[1]))) == 4
    assert len(list(payload_ids(opened(by_name["nodeinfo"])[1]))) == 1


def test_the_special_senders_are_what_the_builder_says() -> None:
    for record in committed():
        if record["name"] in SPECIAL_SENDERS:
            service = envelope(record)
            assert service is not None
            assert getattr(service.packet, "from") == SPECIAL_SENDERS[record["name"]]


def test_every_decoded_coordinate_is_in_the_invented_box() -> None:
    found = 0
    for record in committed():
        for latitude_i, longitude_i in coordinates(record):
            found += 1
            if (latitude_i, longitude_i) == (0, 0):
                continue
            assert LATITUDE_BOX[0] < latitude_i < LATITUDE_BOX[1], record["name"]
            assert LONGITUDE_BOX[0] < longitude_i < LONGITUDE_BOX[1], record["name"]
    assert found >= 7


def called_names(source: str) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            function = node.func
            if isinstance(function, ast.Attribute):
                names.add(function.attr)
            elif isinstance(function, ast.Name):
                names.add(function.id)
    return names


def test_the_builder_reads_no_file() -> None:
    source = (FIXTURES / "build.py").read_text(encoding="utf-8")
    assert called_names(source).isdisjoint(READING_CALLS)
    assert "mesh-terrain-data" not in source


@pytest.mark.parametrize("call", ["json.load(handle)", "gzip.open(path)", "Path('x').glob('*')"])
def test_the_reading_check_sees_attribute_calls(call: str) -> None:
    assert not called_names(call).isdisjoint(READING_CALLS)
