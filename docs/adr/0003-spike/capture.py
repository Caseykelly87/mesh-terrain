"""Throwaway MQTT capture for the Phase 1 spike (ADR 0003).

Subscribes to the public Meshtastic broker, buffers every delivery as one
record (received_at from our clock in UTC, topic, payload as base64) and
appends the buffer to an hourly JSON lines file once a minute. It decodes
nothing and never logs a payload or a topic (topics end in the gateway's
node id). It is not the production ingester and does not write Parquet.
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


class Buffer:
    """Thread-safe list of records with an append-to-hourly-file flush."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.records: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.total = 0
        self.last_message_at: float | None = None

    def add(self, topic: str, payload: bytes) -> int:
        record = {
            "received_at": datetime.now(UTC).isoformat(timespec="microseconds"),
            "topic": topic,
            "payload_b64": base64.b64encode(payload).decode("ascii"),
        }
        with self.lock:
            self.records.append(record)
            self.total += 1
            self.last_message_at = time.monotonic()
            return len(self.records)

    def flush(self) -> None:
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

    def on_connect(client: mqtt.Client, _userdata: Any, _flags: Any, reason: Any, _props: Any) -> None:
        log.info("connect", reason=str(reason), topics=topics)
        for topic in topics:
            client.subscribe(topic, qos=0)

    def on_disconnect(_client: mqtt.Client, _userdata: Any, _flags: Any, reason: Any, _props: Any) -> None:
        log.warning("disconnect", reason=str(reason), last_message_age_s=buffer.last_message_age_s())

    def on_message(_client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage) -> None:
        if buffer.add(message.topic, bytes(message.payload)) >= FLUSH_MAX_RECORDS:
            buffer.flush()

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    return client


def main() -> None:
    data_dir = Path(os.environ.get("DATA_DIR", "/data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    topics = [topic.strip() for topic in os.environ["MQTT_TOPICS"].split(",") if topic.strip()]
    buffer = Buffer(data_dir)
    client = build_client(topics, buffer)
    stop = threading.Event()

    def on_signal(signum: int, _frame: Any) -> None:
        log.info("signal", signum=signum)
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    host, port = os.environ["MQTT_HOST"], int(os.environ.get("MQTT_PORT", "1883"))
    log.info("start", host=host, port=port, data_dir=str(data_dir))
    client.connect_async(host, port, keepalive=60)
    client.loop_start()
    try:
        while not stop.wait(FLUSH_INTERVAL_S):
            buffer.flush()
    finally:
        client.loop_stop()
        client.disconnect()
        buffer.flush()
        log.info("stop", total=buffer.total)


if __name__ == "__main__":
    main()
