"""Throwaway MQTT capture for the Phase 1 spike (ADR 0003).

Subscribes to the public Meshtastic broker, buffers every delivery as one
record (received_at from our clock in UTC, topic, payload as base64) and
appends the buffer to an hourly JSON lines file once a minute. It decodes
nothing and never logs a payload or a topic (topics end in the gateway's
node id). It is not the production ingester and does not write Parquet.

With CAPTURE_THIN=1 it keeps headers only: each record carries the parsed
envelope header, the decode status and the payload length, and drops the
bytes. That is the thinning rule from the brief addendum for when two
captures together would pass the disk line; it is never the default.

It stops itself. At END_AT (ISO 8601 with an offset, optional) or once the
capture files in DATA_DIR reach MAX_RAW_BYTES (default 10 GB, measured after
the once-a-minute flush) it flushes, writes HALTED.json with the reason and
byte count, logs at error and exits 0. With `--restart on-failure` Docker
leaves it stopped. A start that finds HALTED.json exits 0 at once, so
restarting the container never resumes a capture; deleting the marker does.
SIGTERM flushes and exits 0 without a marker, so a stop before END_AT ends
the capture early; the `signal` and `stop` log lines are the only record.
"""

from __future__ import annotations

import base64
import json
import os
import signal
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
import structlog

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.JSONRenderer(),
    ]
)
log = structlog.get_logger(service="spike-capture")

FLUSH_INTERVAL_S = 60.0
FLUSH_MAX_RECORDS = 20000
THIN = os.environ.get("CAPTURE_THIN", "") == "1"
MAX_RAW_BYTES = int(os.environ.get("MAX_RAW_BYTES", str(10 * 10**9)))
END_AT = os.environ.get("END_AT", "")
HALT_MARKER = "HALTED.json"
THIN_FIELDS = (
    "kind",
    "status",
    "reason",
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


def thin_record(received_at: str, topic: str, payload: bytes) -> dict[str, Any]:
    """Header fields and decode status of one delivery, without the bytes."""
    import analyze

    kind = analyze.topic_kind(topic)
    record: dict[str, Any] = {"received_at": received_at, "topic": topic, "thin": True}
    record["payload_len"] = len(payload)
    if kind in {"e", "map"}:
        parsed = analyze.parse_envelope(0.0, kind, analyze.topic_region(topic), payload)
        record.update({name: getattr(parsed, name) for name in THIN_FIELDS})
    else:
        record["kind"] = kind
    return record


class Buffer:
    """Thread-safe list of records with an append-to-hourly-file flush."""

    def __init__(self, data_dir: Path, end_at: datetime | None = None) -> None:
        self.data_dir = data_dir
        self.end_at = end_at
        self.records: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        # The MQTT thread and the main loop can both flush; one writer at a time.
        self.write_lock = threading.Lock()
        self.total = 0
        self.last_message_at: float | None = None

    def add(self, topic: str, payload: bytes) -> int:
        now = datetime.now(UTC)
        if self.end_at is not None and now >= self.end_at:
            # The main loop halts within a second of END_AT; nothing after it lands.
            return 0
        received_at = now.isoformat(timespec="microseconds")
        if THIN:
            record = thin_record(received_at, topic, payload)
        else:
            record = {
                "received_at": received_at,
                "topic": topic,
                "payload_b64": base64.b64encode(payload).decode("ascii"),
            }
        with self.lock:
            self.records.append(record)
            self.total += 1
            self.last_message_at = time.monotonic()
            return len(self.records)

    def flush(self) -> None:
        with self.write_lock:
            self.write()

    def write(self) -> None:
        with self.lock:
            pending, self.records = self.records, []
        if not pending:
            log.info("flush_empty", total=self.total, last_message_age_s=self.last_message_age_s())
            return
        started = time.monotonic()
        by_hour: dict[str, list[dict[str, Any]]] = {}
        for record in pending:
            by_hour.setdefault(record["received_at"][:13], []).append(record)
        for hour, records in by_hour.items():
            path = self.data_dir / f"capture-{hour}.jsonl"
            text = "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records)
            with path.open("a", encoding="ascii") as handle:
                handle.write(text)
            log.info(
                "flush",
                path=str(path),
                count=len(records),
                bytes=len(text),
                duration_s=round(time.monotonic() - started, 3),
                total=self.total,
                last_message_age_s=self.last_message_age_s(),
            )

    def last_message_age_s(self) -> float | None:
        if self.last_message_at is None:
            return None
        return round(time.monotonic() - self.last_message_at, 1)


def build_client(topics: list[str], buffer: Buffer) -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="", clean_session=True)
    client.username_pw_set(os.environ["MQTT_USER"], os.environ["MQTT_PASSWORD"])
    client.reconnect_delay_set(min_delay=1, max_delay=120)

    def on_connect(
        client: mqtt.Client, _userdata: Any, _flags: Any, reason: Any, _props: Any
    ) -> None:
        log.info("connect", reason=str(reason), topics=topics)
        for topic in topics:
            client.subscribe(topic, qos=0)

    def on_disconnect(
        _client: mqtt.Client, _userdata: Any, _flags: Any, reason: Any, _props: Any
    ) -> None:
        log.warning(
            "disconnect", reason=str(reason), last_message_age_s=buffer.last_message_age_s()
        )

    def on_message(_client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage) -> None:
        if buffer.add(message.topic, bytes(message.payload)) >= FLUSH_MAX_RECORDS:
            buffer.flush()

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    return client


def parse_end_at(value: str) -> datetime | None:
    if not value:
        return None
    end_at = datetime.fromisoformat(value)
    if end_at.tzinfo is None:
        raise ValueError(f"END_AT needs an offset, for example {value}+00:00")
    return end_at


def landing_bytes(data_dir: Path) -> int:
    return sum(path.stat().st_size for path in data_dir.glob("capture-*.jsonl*"))


def halt_reason(data_dir: Path, end_at: datetime | None) -> str | None:
    if end_at is not None and datetime.now(UTC) >= end_at:
        return "end_at"
    if landing_bytes(data_dir) >= MAX_RAW_BYTES:
        return "max_raw_bytes"
    return None


def write_halt_marker(data_dir: Path, reason: str, total: int) -> None:
    size = landing_bytes(data_dir)
    marker = {
        "reason": reason,
        "halted_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "landing_bytes": size,
        "max_raw_bytes": MAX_RAW_BYTES,
        "end_at": END_AT or None,
        "records_this_run": total,
    }
    path = data_dir / HALT_MARKER
    path.write_text(json.dumps(marker) + "\n", encoding="ascii")
    log.error("halt", reason=reason, landing_bytes=size, marker=str(path), total=total)


def wait_s(end_at: datetime | None) -> float:
    """Seconds to the next flush, cut short so END_AT is met to the second."""
    if end_at is None:
        return FLUSH_INTERVAL_S
    remaining = (end_at - datetime.now(UTC)).total_seconds()
    return max(0.0, min(FLUSH_INTERVAL_S, remaining))


def main() -> None:
    data_dir = Path(os.environ.get("DATA_DIR", "/data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    marker = data_dir / HALT_MARKER
    if marker.exists():
        log.error("halted", marker=str(marker), detail="delete the marker to resume")
        return
    end_at = parse_end_at(END_AT)
    halted = halt_reason(data_dir, end_at)
    if halted is not None:
        write_halt_marker(data_dir, halted, 0)
        return
    topics = [topic.strip() for topic in os.environ["MQTT_TOPICS"].split(",") if topic.strip()]
    buffer = Buffer(data_dir, end_at)
    client = build_client(topics, buffer)
    stop = threading.Event()

    def on_signal(signum: int, _frame: Any) -> None:
        log.info("signal", signum=signum)
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    host, port = os.environ["MQTT_HOST"], int(os.environ.get("MQTT_PORT", "1883"))
    log.info(
        "start",
        host=host,
        port=port,
        data_dir=str(data_dir),
        thin=THIN,
        end_at=END_AT or None,
        max_raw_bytes=MAX_RAW_BYTES,
    )
    client.connect_async(host, port, keepalive=60)
    client.loop_start()
    try:
        while not stop.wait(wait_s(end_at)):
            buffer.flush()
            halted = halt_reason(data_dir, end_at)
            if halted is not None:
                break
    finally:
        client.loop_stop()
        client.disconnect()
        buffer.flush()
        if halted is not None:
            write_halt_marker(data_dir, halted, buffer.total)
        log.info("stop", total=buffer.total)


if __name__ == "__main__":
    main()
