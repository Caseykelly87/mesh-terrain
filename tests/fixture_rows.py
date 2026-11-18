"""Rows shaped like the raw tables, built from the synthetic fixtures the dbt models are tested on.

Two fixtures feed the dbt build in `tests/test_dbt_fixture_build.py`:

- `tests/fixtures/link_rules/`, the link-rules conformance fixture, one
  snapshot `link-rules-fixture`, with the defaults its README gives for raw's
  other not-null columns;
- `tests/fixtures/staging_cases/`, warehouse-eng's cases for what the link
  rules fixture cannot hold: one packet in two snapshots (issue #55 case 35)
  and positions on the MSA edge and at precisions it does not use. Each of
  its messages names its own `snapshot_id`.

Nothing here connects to a database; `load_rows` takes an open connection and
the schema the caller created.
"""

import json
import math
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from ingester.allowlist import MQTT_MESSAGE

REPO_ROOT = Path(__file__).resolve().parents[1]
LINK_RULES_CASES = REPO_ROOT / "tests" / "fixtures" / "link_rules" / "cases"
STAGING_CASES = REPO_ROOT / "tests" / "fixtures" / "staging_cases" / "cases"
LINK_RULES_SNAPSHOT = "link-rules-fixture"
MESSAGE_COLUMNS = tuple(column.name for column in MQTT_MESSAGE)

# The link-rules README, "Loading and comparing": raw's other not-null columns.
DEFAULTS: dict[str, Any] = {
    "decode_status": "ok",
    "hop_limit": 0,
    "payload_length": 0,
    "decoder_version": "fixture",
    "schema_version": 1,
}


def case_files(folder: Path) -> list[Path]:
    """The case files of one fixture, in case-number order."""
    return sorted(folder.glob("*.json"))


def read_cases(folder: Path) -> list[dict[str, Any]]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in case_files(folder)]


def default_topic(message: dict[str, Any]) -> str:
    return f"msh/US/2/e/LongFast/{message['gateway_pseudonym']}"


def raw_value(column: str, value: Any) -> Any:
    """A fixture value as raw stores it: timestamps parsed, the text "NaN" as the float NaN."""
    if value is None:
        return None
    if column == "received_at":
        return datetime.fromisoformat(value)
    if column == "rx_snr" and value == "NaN":
        return math.nan
    return value


def message_row(message: dict[str, Any], snapshot_id: str) -> dict[str, Any]:
    """One raw.mqtt_message row: every column, the README's defaults, null where absent."""
    filled = {**DEFAULTS, "topic": default_topic(message), "snapshot_id": snapshot_id, **message}
    return {column: raw_value(column, filled.get(column)) for column in MESSAGE_COLUMNS}


def link_rules_rows() -> list[dict[str, Any]]:
    return [
        message_row(message, LINK_RULES_SNAPSHOT)
        for case in read_cases(LINK_RULES_CASES)
        for message in case["messages"]
    ]


def staging_rows() -> list[dict[str, Any]]:
    """The staging cases' rows; each message carries its snapshot_id."""
    rows = []
    for case in read_cases(STAGING_CASES):
        for message in case["messages"]:
            fields = {key: value for key, value in message.items() if key != "snapshot_id"}
            rows.append(message_row(fields, message["snapshot_id"]))
    return rows


def all_rows() -> list[dict[str, Any]]:
    return [*link_rules_rows(), *staging_rows()]


def snapshot_ids(rows: Iterable[dict[str, Any]]) -> list[str]:
    return sorted({row["snapshot_id"] for row in rows})


def snapshot_row(snapshot_id: str) -> dict[str, Any]:
    """A raw.snapshot row for a synthetic fixture snapshot: no file, so no manifest to check."""
    return {
        "snapshot_id": snapshot_id,
        "capture_date": date(2026, 10, 22),
        "source_url": "synthetic fixture, no source",
        "licence": "synthetic, none",
        "manifest_path": "none",
        "manifest_sha256": "none",
        "registered_at": datetime.fromisoformat("2026-10-22T00:00:00+00:00"),
    }


def insert(conn: psycopg.Connection, schema: str, table: str, rows: list[dict[str, Any]]) -> None:
    columns = list(rows[0])
    statement = sql.SQL("insert into {} ({}) values ({})").format(
        sql.Identifier(schema, table),
        sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.SQL(", ").join(sql.Placeholder() * len(columns)),
    )
    with conn.cursor() as cursor:
        cursor.executemany(statement, [[row[column] for column in columns] for row in rows])


def load_rows(conn: psycopg.Connection, schema: str, rows: list[dict[str, Any]]) -> None:
    """Insert the snapshots the rows name, then the rows, into the raw tables of schema.

    Args:
        conn: An open connection; the caller commits.
        schema: A throwaway schema that already holds the raw tables.
    """
    new_snapshots = [
        snapshot_row(snapshot_id)
        for snapshot_id in snapshot_ids(rows)
        if not snapshot_registered(conn, schema, snapshot_id)
    ]
    if new_snapshots:
        insert(conn, schema, "snapshot", new_snapshots)
    insert(conn, schema, "mqtt_message", rows)


def snapshot_registered(conn: psycopg.Connection, schema: str, snapshot_id: str) -> bool:
    query = sql.SQL("select count(*) from {} where snapshot_id = %s").format(
        sql.Identifier(schema, "snapshot")
    )
    row = conn.execute(query, (snapshot_id,)).fetchone()
    return bool(row and row[0])

