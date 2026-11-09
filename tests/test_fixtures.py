"""The synthetic MQTT fixtures are built, never recorded (ADR 0005, prv_01).

The committed deliveries.jsonl must equal a fresh build, so nothing in it can
have come from a capture; every node id in it must lie in the invented range
and none may be on the denylist; every position must be in the invented
South Atlantic box; and the builder must not read any file.
"""

import base64
import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType

import pytest
from google.protobuf.message import DecodeError
from meshtastic.protobuf import mesh_pb2, mqtt_pb2

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "mqtt"
NODE_PATTERN = re.compile(r"!([0-9a-f]{8})")
INVENTED_RANGE = range(0xF1C70000, 0xF1C80000)
# Senders the builder sets on purpose to a value outside the range.
SPECIAL_SENDERS = {"from_zero": 0}
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
    "both_brokers",
    "repeat",
    "stripped_public_whole_meshstl",
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


def envelope(record: dict[str, str]) -> mqtt_pb2.ServiceEnvelope | None:
    service = mqtt_pb2.ServiceEnvelope()
    try:
        service.ParseFromString(base64.b64decode(record["payload_b64"]))
    except DecodeError:
        return None
    return service


def test_committed_fixtures_equal_a_fresh_build() -> None:
    assert (FIXTURES / "deliveries.jsonl").read_text(encoding="utf-8") == load_builder().render()


def test_every_packet_type_of_the_brief_has_a_fixture() -> None:
    assert {record["name"] for record in committed()} == EXPECTED_NAMES


def test_both_broker_fixtures_have_one_delivery_per_broker() -> None:
    brokers = [record["broker"] for record in committed() if record["name"] == "both_brokers"]
    assert sorted(brokers) == ["meshstl", "public"]
    pair = [record["payload_b64"] for record in committed() if record["name"] == "both_brokers"]
    assert pair[0] == pair[1]


def node_ids(record: dict[str, str]) -> list[int]:
    """Gateway ids in the topic and envelope, and the sender, of one record."""
    ids = [int(match, 16) for match in NODE_PATTERN.findall(record["topic"])]
    service = envelope(record)
    if service is not None and service.HasField("packet"):
        ids += [int(match, 16) for match in NODE_PATTERN.findall(service.gateway_id)]
        sender = getattr(service.packet, "from")
        if record["name"] not in SPECIAL_SENDERS:
            ids.append(sender)
    return ids


def test_every_node_id_is_invented_and_none_is_denylisted() -> None:
    banned = denylist()
    for record in committed():
        for node_id in node_ids(record):
            assert node_id in INVENTED_RANGE, record["name"]
            assert node_id not in banned, record["name"]


def test_the_special_senders_are_what_the_builder_says() -> None:
    for record in committed():
        if record["name"] in SPECIAL_SENDERS:
            service = envelope(record)
            assert service is not None
            assert getattr(service.packet, "from") == SPECIAL_SENDERS[record["name"]]


def test_plaintext_positions_are_invented() -> None:
    builder = load_builder()
    message = mesh_pb2.Position()
    message.ParseFromString(builder.position())
    assert -31.0 < message.latitude_i / 1e7 < -29.0
    assert -26.0 < message.longitude_i / 1e7 < -24.0


@pytest.mark.parametrize("word", ["open(", "read_text", "read_bytes", "mesh-terrain-data", "glob"])
def test_the_builder_reads_no_file(word: str) -> None:
    source = (FIXTURES / "build.py").read_text(encoding="utf-8")
    assert word not in source
