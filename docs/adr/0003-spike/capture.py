"""On-demand MQTT capture command: one run, one snapshot (ADR 0005 item 1).

Kept here, where the ADR 0003 spike capture ran, by Casey's decision. It
connects to one broker, subscribes to a topic list, and hands every delivery
to ingester.capture.CaptureRun, which applies the ADR 0005 allowlist and the
HMAC pseudonyms in memory and writes zstd Parquet by hour into
`<snapshots>/<snapshot id>.partial/`. Once a second it flushes when due and
checks the stop rules; at END_AT or MAX_RAW_BYTES (measured on the snapshot
folder), or on SIGTERM or Ctrl-C, it merges the flush files, writes the README
and the sha256 manifest with the stop reason, renames the folder to
`<snapshot id>/` and exits 0. A write that fails twice exits non-zero and
leaves the `.partial` folder without a manifest for Casey to look at.

There is no halt marker, no resume and no restart: run it by hand, never
under a restart policy or a schedule. A run against the MeshSTL broker needs
Casey's approval every time and refuses to start without
`--meshstl-approved-by-casey DATE`. Nothing it logs or writes holds a payload,
a topic with a real gateway id or a real node id.

Usage (README.md beside this file has the worked example):

    uv run --frozen python -I docs/adr/0003-spike/capture.py \
        --host mqtt.meshtastic.org --source public \
        --topic 'msh/US/MeshSTL/#' --topic 'msh/US/MO/#' \
        --end-at 2026-12-01T00:00:00+00:00 --snapshot-id meshtastic-capture-2026-11-30
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
import structlog

from ingester.capture import CaptureRun, RunParameters
from ingester.flush import parse_end_at
from ingester.settings import get_ingester_settings
from ingester.sightings import Broker

log = structlog.get_logger(component="capture")
MESHSTL_HOST = "mqtt.meshstl.org"
DEFAULT_MAX_RAW_BYTES = 10_000_000_000


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One on-demand capture run (ADR 0005).")
    parser.add_argument("--host", required=True, help="broker host, e.g. mqtt.meshtastic.org")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--source", choices=[b.value for b in Broker], required=True,
                        help="which broker flag the rows carry: public or meshstl")
    parser.add_argument("--topic", action="append", required=True, dest="topics",
                        help="topic filter; repeat for several")
    parser.add_argument("--end-at", required=True, type=parse_end_at,
                        help="ISO 8601 with an offset; nothing at or after it is kept")
    parser.add_argument("--max-raw-bytes", type=int, default=DEFAULT_MAX_RAW_BYTES,
                        help="stop when the snapshot folder reaches this many bytes")
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--snapshots", type=Path, default=None,
                        help="snapshots folder; SNAPSHOTS_DIR by default")
    parser.add_argument("--meshstl-approved-by-casey", metavar="DATE", default=None,
                        help="the date of Casey's approval; required for the MeshSTL broker")
    return parser.parse_args(argv)


def build_client(run: CaptureRun, topics: list[str], user: str, password: str) -> mqtt.Client:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="", clean_session=True)
    client.username_pw_set(user, password)
    client.reconnect_delay_set(min_delay=1, max_delay=120)

    def on_connect(client: mqtt.Client, _data: Any, _flags: Any, reason: Any, _props: Any) -> None:
        log.info("connect", reason=str(reason), topic_filters=len(topics))
        for topic in topics:
            client.subscribe(topic, qos=0)

    def on_disconnect(_client: mqtt.Client, _data: Any, _flags: Any, reason: Any, _p: Any) -> None:
        log.warning("disconnect", reason=str(reason))

    def on_message(_client: mqtt.Client, _data: Any, message: mqtt.MQTTMessage) -> None:
        run.receive(message.topic, bytes(message.payload))

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    return client


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.host == MESHSTL_HOST and not args.meshstl_approved_by_casey:
        log.error("meshstl_needs_approval", host=args.host)
        return 2
    settings = get_ingester_settings()
    params = RunParameters(
        host=args.host,
        port=args.port,
        source=Broker(args.source),
        topics=args.topics,
        end_at=args.end_at,
        max_raw_bytes=args.max_raw_bytes,
        snapshot_id=args.snapshot_id,
        meshstl_approval=args.meshstl_approved_by_casey,
    )
    run = CaptureRun(args.snapshots or settings.snapshots_dir, params, settings.hmac_key())
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    password = settings.mqtt_password.get_secret_value()
    client = build_client(run, args.topics, settings.mqtt_user, password)
    log.info("start", host=args.host, port=args.port, end_at=args.end_at.isoformat(),
             max_raw_bytes=args.max_raw_bytes, snapshot_id=args.snapshot_id,
             meshstl_approval=args.meshstl_approved_by_casey)
    client.connect_async(args.host, args.port, keepalive=60)
    client.loop_start()
    reason = "signal"
    try:
        while not stop.wait(1.0):
            run.maybe_flush()
            found = run.stop_reason()
            if found is not None:
                reason = found
                break
    finally:
        client.loop_stop()
        client.disconnect()
    run.finish(reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
