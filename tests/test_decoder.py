"""The decoder on the synthetic fixtures (ADR 0005 items 2 to 5, ing_01, ing_03, prv_01)."""

import base64
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from ingester.allowlist import DECODE_STATUSES, DECODER_VERSION, SCHEMA_VERSION, allowlist
from ingester.decoder import (
    DECODED_COLUMNS,
    CopyDelivery,
    DroppedDelivery,
    StoredDelivery,
    decode_delivery,
)
from ingester.keys import delivery_key, pseudonym

KEY = bytes(range(32))
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "mqtt" / "deliveries.jsonl"
GATEWAY = 0xF1C70001
GATEWAY_TWO = 0xF1C70002
SENDER = 0xF1C70011
NEIGHBOUR = 0xF1C70033
NODE_PATTERN = re.compile(r"![0-9a-f]{8}")
INVENTED_WORDS = ("kestrel", "Invented Fixture Node", "IFN1", "0.0.0.invented")


def records() -> list[dict[str, str]]:
    return [json.loads(line) for line in FIXTURES.read_text(encoding="utf-8").splitlines()]


def decode(record: dict[str, str]) -> StoredDelivery | CopyDelivery | DroppedDelivery:
    return decode_delivery(
        record["topic"],
        base64.b64decode(record["payload_b64"]),
        datetime.fromisoformat(record["received_at"]),
        KEY,
    )


def outcome(name: str) -> StoredDelivery | CopyDelivery | DroppedDelivery:
    return decode(next(record for record in records() if record["name"] == name))


def row(name: str) -> dict[str, Any]:
    result = outcome(name)
    assert isinstance(result, StoredDelivery), name
    return result.row


def test_rows_carry_exactly_the_allowlist_less_snapshot_and_broker_flags() -> None:
    landed = set(allowlist("mqtt_message")) - {"snapshot_id", "seen_public", "seen_meshstl"}
    assert set(DECODED_COLUMNS) == landed
    for record in records():
        result = decode(record)
        if isinstance(result, StoredDelivery):
            assert list(result.row) == list(DECODED_COLUMNS), record["name"]


def test_every_row_carries_both_versions_and_a_known_status() -> None:
    for record in records():
        result = decode(record)
        if isinstance(result, StoredDelivery):
            assert result.row["decoder_version"] == DECODER_VERSION
            assert result.row["schema_version"] == SCHEMA_VERSION
            assert result.row["decode_status"] in DECODE_STATUSES


def test_position_row() -> None:
    record = next(record for record in records() if record["name"] == "position")
    payload = base64.b64decode(record["payload_b64"])
    position = row("position")
    assert position["delivery_key"] == delivery_key(KEY, record["topic"], payload)
    assert position["received_at"] == datetime.fromisoformat(record["received_at"])
    assert position["topic"] == "msh/US/Example/2/e/LongFast/" + pseudonym(KEY, GATEWAY)
    assert position["modem_preset"] == "LongFast"
    assert position["decode_status"] == "ok"
    assert position["gateway_pseudonym"] == pseudonym(KEY, GATEWAY)
    assert position["gateway_low_byte"] == 0x01
    assert position["origin_pseudonym"] == pseudonym(KEY, SENDER)
    assert position["origin_low_byte"] == 0x11
    assert position["packet_id"] == 0x1001
    assert position["relay_node"] == 0x22
    assert (position["hop_start"], position["hop_limit"]) == (3, 3)
    assert position["via_mqtt"] is False
    assert position["rx_time"] == 1767250800
    assert position["rx_snr"] == 6.25
    assert position["rx_rssi"] == -97
    assert position["channel"] == 8
    assert position["portnum"] == 3
    assert position["payload_length"] == len(payload)
    assert position["position_source"] == "position"
    assert (position["latitude_i"], position["longitude_i"]) == (-301234567, -251234567)
    assert (position["altitude_m"], position["precision_bits"]) == (123, 16)
    assert position["position_time"] == 1767250700
    assert position["location_source"] == "LOC_INTERNAL"
    assert position["altitude_source"] == "ALT_INTERNAL"


def test_mapreport_on_an_e_topic_lands_its_four_fields_only() -> None:
    report = row("mapreport_e")
    assert report["portnum"] == 73
    assert report["position_source"] == "mapreport"
    assert (report["latitude_i"], report["longitude_i"]) == (-301233567, -251233567)
    assert (report["altitude_m"], report["precision_bits"]) == (45, 13)
    assert report["hw_model"] is None and report["role"] is None
    assert report["position_time"] is None


def test_mapreport_on_a_map_topic() -> None:
    report = row("mapreport_map")
    assert report["topic"] == "msh/US/Example/2/map/"
    assert report["position_source"] == "mapreport"
    assert report["precision_bits"] == 13
    assert report["modem_preset"] is None


def test_nodeinfo_lands_hardware_model_and_role_only() -> None:
    info = row("nodeinfo")
    assert (info["hw_model"], info["role"]) == ("RAK4631", "CLIENT")
    assert info["position_source"] is None


def test_neighborinfo_lands_pseudonyms_low_bytes_snr_and_last_heard() -> None:
    info = row("neighborinfo")
    assert info["neighbour_pseudonyms"] == [pseudonym(KEY, NEIGHBOUR), pseudonym(KEY, GATEWAY)]
    assert info["neighbour_low_bytes"] == [0x33, 0x01]
    assert info["neighbour_snrs"] == [4.5, -3.25]
    assert info["neighbour_last_heard"] == [1767250600, 1767250650]


def test_telemetry_lands_four_device_metrics() -> None:
    metrics = row("telemetry")
    assert metrics["channel_utilization"] == pytest.approx(12.5)
    assert metrics["air_util_tx"] == pytest.approx(1.75)
    assert metrics["uptime_s"] == 86400
    assert metrics["battery_level"] == 87


def test_text_message_is_a_row_with_its_portnum_and_no_text() -> None:
    text = row("text")
    assert (text["decode_status"], text["portnum"]) == ("ok", 1)


@pytest.mark.parametrize(
    ("name", "status", "portnum"),
    [
        ("unknown_port", "unknown_port", 92),
        ("stripped", "stripped", None),
        ("wrong_key", "decrypt_failed", None),
        ("pki", "decrypt_failed", None),
        ("portnum_zero", "malformed", 0),
        ("portnum_negative", "malformed", -3),
        ("packet_id_zero", "decrypt_failed", None),
        ("bad_position_payload", "malformed", 3),
        ("decrypted_portnum_zero", "decrypt_failed", None),
        ("decrypted_portnum_above_max", "decrypt_failed", None),
    ],
)
def test_decode_status(name: str, status: str, portnum: int | None) -> None:
    result = row(name)
    assert (result["decode_status"], result["portnum"]) == (status, portnum)


def test_packet_id_zero_lands_as_null() -> None:
    assert row("packet_id_zero")["packet_id"] is None


@pytest.mark.parametrize(
    "field", ["relay_node", "hop_start", "rx_time", "rx_snr", "rx_rssi", "channel"]
)
def test_unset_header_fields_land_as_null(field: str) -> None:
    unset = row("unset_header")
    assert unset[field] is None
    assert unset["hop_limit"] == 3
    assert unset["modem_preset"] == "MediumFast"


def test_an_internet_copy_is_counted_with_its_gateway() -> None:
    copy = outcome("via_mqtt_copy")
    assert copy == CopyDelivery(
        topic="msh/US/Example/2/e/LongFast/" + pseudonym(KEY, GATEWAY_TWO),
        gateway_pseudonym=pseudonym(KEY, GATEWAY_TWO),
        gateway_low_byte=0x02,
    )


@pytest.mark.parametrize(
    ("name", "topic_class", "reason"),
    [
        ("malformed_envelope", "e", "unparsed"),
        ("from_zero", "e", "unparsed"),
        ("map_unparsed", "map", "unparsed"),
        ("json", "json", "json"),
        ("stat", "stat", "stat"),
        ("other_topic", "other", "other"),
    ],
)
def test_dropped_deliveries(name: str, topic_class: str, reason: str) -> None:
    assert outcome(name) == DroppedDelivery(topic_class, reason)


def test_both_broker_fixtures_decode_to_one_key() -> None:
    pair = [decode(record) for record in records() if record["name"] == "both_brokers"]
    keys = {result.row["delivery_key"] for result in pair if isinstance(result, StoredDelivery)}
    assert len(keys) == 1


def test_stripped_and_whole_copies_decode_to_two_keys() -> None:
    pair = [
        decode(record) for record in records() if record["name"] == "stripped_public_whole_meshstl"
    ]
    rows = [result.row for result in pair if isinstance(result, StoredDelivery)]
    assert [item["decode_status"] for item in rows] == ["stripped", "ok"]
    assert rows[0]["delivery_key"] != rows[1]["delivery_key"]


def stored_strings() -> list[str]:
    strings: list[str] = []
    for record in records():
        result = decode(record)
        values = list(result.row.values()) if isinstance(result, StoredDelivery) else []
        if isinstance(result, CopyDelivery):
            values = [result.topic, result.gateway_pseudonym]
        for value in values:
            items = value if isinstance(value, list) else [value]
            strings += [item for item in items if isinstance(item, str)]
            assert not any(isinstance(item, bytes | bytearray) for item in items)
    return strings


def test_no_output_holds_a_node_id_or_invented_text_or_names() -> None:
    # prv_01: no real identifier, text, long or short name lands; no bytes at all.
    strings = stored_strings()
    assert strings
    for value in strings:
        assert NODE_PATTERN.search(value) is None, value
        for word in INVENTED_WORDS:
            assert word not in value
