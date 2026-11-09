"""Decode one MQTT delivery into an allowlisted row, an internet copy or a counted drop.

A delivery on an `e` or `map` topic is parsed as a ServiceEnvelope with the
pinned meshtastic protobufs. With no sender, or when it does not parse, it is
counted as `unparsed`. An internet copy (`via_mqtt`) on an `e` topic is counted
with its gateway. Anything else on those topics becomes one stored row: the
header, a decode status, and the allowlisted fields of its packet type after
AES-CTR decryption with the documented default key where needed (ADR 0005).
`json`, `stat` and `other` topics are counted, never parsed. The delivery key
is computed from the bytes before they are dropped; no payload byte, text,
name or public key leaves this module.

Statuses (ing_03): `stripped` when the envelope has no payload; `decrypt_failed`
for PKI packets, a zero packet id, a payload that does not parse after
decryption, or a decrypted portnum of 0 or above the enum's maximum (the
wrong-key signs the ADR 0003 spike used); `malformed` for a portnum below 1 in
a packet that parsed, or a payload that fails to parse as its portnum's
message; `unknown_port` for a portnum the pinned enum does not name; `ok`
otherwise. A decode failure is a status, never an exception.
"""

import base64
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from google.protobuf.message import DecodeError, Message  # type: ignore[import-untyped]
from meshtastic.protobuf import (  # type: ignore[import-untyped]
    config_pb2,
    mesh_pb2,
    mqtt_pb2,
    portnums_pb2,
    telemetry_pb2,
)

from ingester.allowlist import DECODER_VERSION, SCHEMA_VERSION, allowlist
from ingester.keys import delivery_key, low_byte, pseudonym
from ingester.topics import landed_topic, modem_preset, topic_class

DEFAULT_KEY = base64.b64decode("1PG7OiApB1nwvP+rz05pAQ==")
DECODED_COLUMNS = tuple(
    name
    for name in allowlist("mqtt_message")
    if name not in {"snapshot_id", "seen_public", "seen_meshstl"}
)
_NAMED_PORTS = frozenset(portnums_pb2.PortNum.values())
_UNSET_IS_NULL = ("relay_node", "hop_start", "rx_time", "rx_snr", "rx_rssi", "channel")


@dataclass(frozen=True)
class StoredDelivery:
    """A delivery stored as one row; row keys are DECODED_COLUMNS in order."""

    row: dict[str, Any]


@dataclass(frozen=True)
class CopyDelivery:
    """An internet copy, counted per (hour, topic, gateway pseudonym)."""

    topic: str
    gateway_pseudonym: str
    gateway_low_byte: int


@dataclass(frozen=True)
class DroppedDelivery:
    """A delivery counted per (hour, topic class, reason) and not stored."""

    topic_class: str
    reason: str


Decoded = StoredDelivery | CopyDelivery | DroppedDelivery


def nonce(packet_id: int, sender: int) -> bytes:
    """Firmware nonce: packet id 8 bytes little-endian, sender 4 bytes little-endian, 4 zeros."""
    return packet_id.to_bytes(8, "little") + sender.to_bytes(4, "little") + b"\x00" * 4


def decrypt_default(ciphertext: bytes, packet_id: int, sender: int) -> bytes:
    """AES-128-CTR with the default channel key."""
    decryptor = Cipher(algorithms.AES(DEFAULT_KEY), modes.CTR(nonce(packet_id, sender))).decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


def gateway_number(gateway_id: str) -> int | None:
    """The node number in a "!xxxxxxxx" gateway id; None when it is not one or is zero."""
    if len(gateway_id) != 9 or not gateway_id.startswith("!"):
        return None
    try:
        number = int(gateway_id[1:], 16)
    except ValueError:
        return None
    return number or None


def _parse_envelope(payload: bytes) -> Any | None:
    envelope = mqtt_pb2.ServiceEnvelope()
    try:
        envelope.ParseFromString(payload)
    except DecodeError:
        return None
    return envelope


def decode_delivery(topic: str, payload: bytes, received_at: datetime, key: bytes) -> Decoded:
    """Decode one delivery as received.

    Args:
        topic: The topic as received, with the real gateway id.
        payload: The MQTT payload bytes as received.
        received_at: Our clock's time of receipt, timezone-aware UTC.
        key: The HMAC key from keys.parse_hmac_key.

    Returns:
        A StoredDelivery, a CopyDelivery or a DroppedDelivery. Never raises on
        bad input bytes.
    """
    kind = topic_class(topic)
    if kind not in {"e", "map"}:
        return DroppedDelivery(kind, kind)
    envelope = _parse_envelope(payload)
    if envelope is None or not getattr(envelope.packet, "from"):
        return DroppedDelivery(kind, "unparsed")
    gateway = gateway_number(envelope.gateway_id)
    if kind == "e" and envelope.packet.via_mqtt:
        number = gateway or 0
        return CopyDelivery(
            landed_topic(topic, key), pseudonym(key, number) if number else "", low_byte(number)
        )
    row = _header_row(topic, payload, received_at, key, envelope, gateway)
    _decode_payload(row, envelope.packet, key)
    return StoredDelivery(row)


def _header_row(
    topic: str,
    payload: bytes,
    received_at: datetime,
    key: bytes,
    envelope: Any,
    gateway: int | None,
) -> dict[str, Any]:
    packet = envelope.packet
    sender = getattr(packet, "from")
    row: dict[str, Any] = dict.fromkeys(DECODED_COLUMNS)
    row.update(
        delivery_key=delivery_key(key, topic, payload),
        received_at=received_at,
        topic=landed_topic(topic, key),
        modem_preset=modem_preset(topic),
        gateway_pseudonym=pseudonym(key, gateway) if gateway else None,
        gateway_low_byte=low_byte(gateway) if gateway else None,
        origin_pseudonym=pseudonym(key, sender),
        origin_low_byte=low_byte(sender),
        packet_id=packet.id or None,
        hop_limit=packet.hop_limit,
        via_mqtt=packet.via_mqtt,
        payload_length=len(payload),
        decoder_version=DECODER_VERSION,
        schema_version=SCHEMA_VERSION,
    )
    for name in _UNSET_IS_NULL:
        row[name] = getattr(packet, name) or None
    return row


def _packet_data(packet: Any) -> tuple[Any | None, str]:
    """The Data message of a packet and the status when there is none."""
    if packet.HasField("decoded"):
        return packet.decoded, "ok"
    if packet.pki_encrypted:
        return None, "decrypt_failed"
    if not packet.encrypted:
        return None, "stripped"
    if not packet.id:
        return None, "decrypt_failed"
    data = mesh_pb2.Data()
    try:
        data.ParseFromString(decrypt_default(packet.encrypted, packet.id, getattr(packet, "from")))
    except DecodeError:
        return None, "decrypt_failed"
    if data.portnum == 0 or data.portnum > portnums_pb2.MAX:
        return None, "decrypt_failed"
    return data, "ok"


def _decode_payload(row: dict[str, Any], packet: Any, key: bytes) -> None:
    data, status = _packet_data(packet)
    row["decode_status"] = status
    if data is None:
        return
    row["portnum"] = data.portnum
    if data.portnum <= 0:
        row["decode_status"] = "malformed"
        return
    decode = _PORT_DECODERS.get(data.portnum)
    try:
        if decode is not None:
            decode(row, data.payload, key)
    except DecodeError:
        row["decode_status"] = "malformed"
        return
    if data.portnum not in _NAMED_PORTS:
        row["decode_status"] = "unknown_port"


def _parse(message: Message, payload: bytes) -> Any:
    message.ParseFromString(payload)
    return message


def _enum_name(enum: Any, value: int) -> str:
    try:
        return str(enum.Name(value))
    except ValueError:
        return str(value)


def _position(row: dict[str, Any], payload: bytes, key: bytes) -> None:
    position = _parse(mesh_pb2.Position(), payload)
    row["position_source"] = "position"
    for field, column in (
        ("latitude_i", "latitude_i"),
        ("longitude_i", "longitude_i"),
        ("altitude", "altitude_m"),
    ):
        row[column] = getattr(position, field) if position.HasField(field) else None
    row["precision_bits"] = position.precision_bits
    row["position_time"] = position.time or None
    row["location_source"] = _enum_name(mesh_pb2.Position.LocSource, position.location_source)
    row["altitude_source"] = _enum_name(mesh_pb2.Position.AltSource, position.altitude_source)


def _map_report(row: dict[str, Any], payload: bytes, key: bytes) -> None:
    report = _parse(mqtt_pb2.MapReport(), payload)
    # MapReport fields have no presence; a report with both coordinates zero
    # carries no location, as the ADR 0003 spike read it.
    if not (report.latitude_i or report.longitude_i):
        return
    row["position_source"] = "mapreport"
    row["latitude_i"] = report.latitude_i
    row["longitude_i"] = report.longitude_i
    row["altitude_m"] = report.altitude
    row["precision_bits"] = report.position_precision


def _node_info(row: dict[str, Any], payload: bytes, key: bytes) -> None:
    user = _parse(mesh_pb2.User(), payload)
    row["hw_model"] = _enum_name(mesh_pb2.HardwareModel, user.hw_model)
    row["role"] = _enum_name(config_pb2.Config.DeviceConfig.Role, user.role)


def _neighbor_info(row: dict[str, Any], payload: bytes, key: bytes) -> None:
    info = _parse(mesh_pb2.NeighborInfo(), payload)
    row["neighbour_pseudonyms"] = [pseudonym(key, item.node_id) for item in info.neighbors]
    row["neighbour_low_bytes"] = [low_byte(item.node_id) for item in info.neighbors]
    row["neighbour_snrs"] = [item.snr for item in info.neighbors]
    row["neighbour_last_heard"] = [item.last_rx_time for item in info.neighbors]


def _telemetry(row: dict[str, Any], payload: bytes, key: bytes) -> None:
    telemetry = _parse(telemetry_pb2.Telemetry(), payload)
    if not telemetry.HasField("device_metrics"):
        return
    metrics = telemetry.device_metrics
    for field, column in (
        ("channel_utilization", "channel_utilization"),
        ("air_util_tx", "air_util_tx"),
        ("uptime_seconds", "uptime_s"),
        ("battery_level", "battery_level"),
    ):
        row[column] = getattr(metrics, field) if metrics.HasField(field) else None


_PORT_DECODERS: dict[int, Callable[[dict[str, Any], bytes, bytes], None]] = {
    portnums_pb2.POSITION_APP: _position,
    portnums_pb2.MAP_REPORT_APP: _map_report,
    portnums_pb2.NODEINFO_APP: _node_info,
    portnums_pb2.NEIGHBORINFO_APP: _neighbor_info,
    portnums_pb2.TELEMETRY_APP: _telemetry,
}
