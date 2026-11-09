"""The README written into a snapshot folder before its manifest (snp_01, ADR 0005 item 1).

It says what the snapshot holds, when and from which brokers it was captured,
under what licence line, which window of each capture it holds, why the run
stopped, the observed hours, and the delivery counts the ing_03 check uses.
It is rendered from the manifest metadata, so the two cannot disagree.
"""

from typing import Any

_TOTALS = ("received", "stored", "counted", "merged_cross_broker", "merged_repeat")


def _totals(deliveries: list[dict[str, Any]], broker: str) -> dict[str, int]:
    rows = [row for row in deliveries if row["broker"] == broker]
    return {name: sum(int(row[name]) for row in rows) for name in _TOTALS}


def _window_line(broker: str, window: list[str | None]) -> str:
    start, end = window
    if start is None and end is None:
        return f"- {broker}: every record of the capture files."
    return f"- {broker}: records received from {start} up to, not including, {end}."


def render_readme(metadata: dict[str, Any]) -> str:
    """README text for a snapshot folder."""
    lines = [
        f"# Snapshot {metadata['snapshot_id']}",
        "",
        "Allowlisted, pseudonymised Meshtastic MQTT deliveries as zstd Parquet, one file per "
        "table and UTC hour (ADR 0005). Node, gateway and neighbour ids are HMAC-SHA-256 "
        "pseudonyms under NODE_ID_HMAC_KEY; no payload bytes, text, names or public keys.",
        "",
        f"Captured from {metadata['capture_date']}: a single capture from 2026-10-22 to "
        "2026-10-25. Brokers: " + ", ".join(metadata["brokers"]) + ".",
        f"Source URLs: {', '.join(metadata['source_url'])}.",
        "",
        f"Licence: {metadata['licence']}",
        "",
        "Windows read:",
        *(_window_line(broker, window) for broker, window in metadata["windows"].items()),
        "",
        f"Stop reason: {metadata['stop_reason']}. END_AT: {metadata['end_at']}. "
        f"MAX_RAW_BYTES: {metadata['max_raw_bytes']}.",
        "",
        "Observed hours (segments of receipts with no gap over 15 minutes, coverage.json):",
        *(f"- {broker}: {hours:.3f}" for broker, hours in metadata["observed_hours"].items()),
        "",
        "Deliveries, totals over the snapshot (per hour and broker in manifest.json):",
    ]
    for broker in metadata["observed_hours"]:
        totals = _totals(metadata["deliveries"], broker)
        lines.append(f"- {broker}: " + ", ".join(f"{k} {v}" for k, v in totals.items()))
    lines += [
        "",
        "Tables: mqtt_message (one row per delivery key), mqtt_copy_count, "
        "mqtt_dropped_count. Columns: ingester/allowlist.py, schema_version "
        f"{metadata['schema_version']}, decoder_version {metadata['decoder_version']}.",
        "",
        "Not in git, not in LFS, never published. Precise MeshSTL positions are never "
        "published. If this folder is lost, its figures are reproducible only by a new "
        "capture under a new snapshot id.",
        "",
    ]
    return "\n".join(lines)
