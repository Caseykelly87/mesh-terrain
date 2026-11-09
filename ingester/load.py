"""Load a finished snapshot folder into the raw schema (ing_01, ing_02, ing_05, ing_07, snp_01).

The loader refuses a `.partial` folder and a folder without a manifest, checks
every file against its sha256 in the manifest, applies the idempotent raw DDL,
refuses when a raw table's columns differ from the allowlist, registers the
snapshot in raw.snapshot and copies each table through a temporary table
with `on conflict do nothing` on the contract keys. Rows are only ever
inserted, so a second load adds nothing. The caller owns the transaction.

Checks for the asset: reconcile (ing_03, against the manifest's deliveries per
hour and broker), decode_rates (ing_04) and content_hashes (snp_01; the
registration time is left out, since it differs on every load).
"""

import argparse
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import psycopg
import structlog
from psycopg import sql

from ingester.allowlist import KEYS, TABLES, allowlist
from ingester.parquet import read_rows, row_digest
from ingester.settings import get_ingester_settings
from ingester.snapshot import (
    MANIFEST_NAME,
    PARTIAL_SUFFIX,
    SnapshotError,
    read_manifest,
    sha256_file,
    verify_manifest,
)
from ingester.topics import topic_class

log = structlog.get_logger(component="ingester.load")

DDL_PATH = Path(__file__).with_name("sql") / "raw_tables.sql"
SNAPSHOT_COLUMNS = (
    "snapshot_id",
    "capture_date",
    "source_url",
    "licence",
    "manifest_path",
    "manifest_sha256",
    "registered_at",
)
SNAPSHOT_CONTENT_COLUMNS = tuple(name for name in SNAPSHOT_COLUMNS if name != "registered_at")
LOAD_ORDER = ("mqtt_message", "mqtt_copy_count", "mqtt_dropped_count")


class SchemaDriftError(RuntimeError):
    """A raw table's columns differ from the allowlist."""


def snapshot_folder(root: Path, snapshot_id: str) -> Path:
    """The finished folder of a snapshot id.

    Raises:
        SnapshotError: for a `.partial` id or folder, or a folder with no manifest.
    """
    if snapshot_id.endswith(PARTIAL_SUFFIX):
        raise SnapshotError(f"{snapshot_id} is an unfinished run; it is never loaded")
    folder = root / snapshot_id
    if not (folder / MANIFEST_NAME).is_file():
        raise SnapshotError(f"{folder} has no manifest; only finished snapshots load")
    return folder


def apply_ddl(conn: psycopg.Connection) -> None:
    """Create the raw tables when they are missing."""
    conn.execute(DDL_PATH.read_text(encoding="utf-8"))


def check_columns(conn: psycopg.Connection) -> None:
    """Refuse when a raw table's columns differ from the allowlist.

    Raises:
        SchemaDriftError: naming the first table that differs.
    """
    expected = {table: allowlist(table) for table in TABLES} | {"snapshot": list(SNAPSHOT_COLUMNS)}
    query = (
        "select column_name from information_schema.columns "
        "where table_schema = 'raw' and table_name = %s order by ordinal_position"
    )
    for table, columns in expected.items():
        found = [row[0] for row in conn.execute(query, (table,)).fetchall()]
        if found != columns:
            raise SchemaDriftError(f"raw.{table} columns differ from the allowlist")


def register(conn: psycopg.Connection, folder: Path, manifest: dict[str, Any]) -> int:
    """Insert the raw.snapshot row; returns 1 when it is new, 0 when it was there."""
    source_url = manifest["source_url"]
    values = (
        manifest["snapshot_id"],
        manifest["capture_date"],
        ", ".join(source_url) if isinstance(source_url, list) else source_url,
        manifest["licence"],
        str(folder / MANIFEST_NAME),
        sha256_file(folder / MANIFEST_NAME),
    )
    cursor = conn.execute(
        "insert into raw.snapshot "
        "(snapshot_id, capture_date, source_url, licence, manifest_path, manifest_sha256, "
        "registered_at) values (%s, %s, %s, %s, %s, %s, now()) "
        "on conflict (snapshot_id) do nothing",
        values,
    )
    return cursor.rowcount


def load_table(conn: psycopg.Connection, folder: Path, table: str) -> int:
    """Copy one table's files into raw; returns the rows inserted."""
    started = time.monotonic()
    columns = allowlist(table)
    # A fresh name per load: temporary tables live until the session ends, and a
    # second load in the same session must not see the first one's rows.
    staging = sql.Identifier(f"load_{table}_{uuid.uuid4().hex[:12]}")
    target = sql.Identifier("raw", table)
    conn.execute(sql.SQL("create temp table {} (like {})").format(staging, target))
    names = sql.SQL(", ").join(sql.Identifier(name) for name in columns)
    with conn.cursor() as cursor:
        with cursor.copy(sql.SQL("copy {} ({}) from stdin").format(staging, names)) as copy:
            for path in sorted((folder / table).glob("*.parquet")):
                for row in read_rows(path):
                    copy.write_row([row[name] for name in columns])
    key = sql.SQL(", ").join(sql.Identifier(name) for name in KEYS[table])
    cursor = conn.execute(
        sql.SQL("insert into {} ({}) select {} from {} on conflict ({}) do nothing").format(
            target, names, names, staging, key
        )
    )
    log.info("load_table", table=table, inserted=cursor.rowcount, duration_s=_since(started))
    return cursor.rowcount


def _since(started: float) -> float:
    return round(time.monotonic() - started, 3)


def load_snapshot(conn: psycopg.Connection, folder: Path) -> dict[str, int]:
    """Load one finished snapshot folder; returns rows inserted per table.

    Raises:
        SnapshotError: if a file differs from the manifest.
        SchemaDriftError: if a raw table's columns differ from the allowlist.
    """
    problems = verify_manifest(folder)
    if problems:
        raise SnapshotError(f"{len(problems)} files differ from the manifest: {problems[:3]}")
    manifest = read_manifest(folder)
    apply_ddl(conn)
    check_columns(conn)
    inserted = {"snapshot": register(conn, folder, manifest)}
    for table in LOAD_ORDER:
        inserted[table] = load_table(conn, folder, table)
    log.info("load_snapshot", snapshot_id=manifest["snapshot_id"], **inserted)
    return inserted


def row_counts(conn: psycopg.Connection, snapshot_id: str) -> dict[str, int]:
    """Rows per raw table for one snapshot."""
    counts: dict[str, int] = {}
    for table in ("snapshot", *LOAD_ORDER):
        query = sql.SQL("select count(*) from {} where snapshot_id = %s").format(
            sql.Identifier("raw", table)
        )
        row = conn.execute(query, (snapshot_id,)).fetchone()
        counts[table] = int(row[0]) if row else 0
    return counts


def reconcile(conn: psycopg.Connection, folder: Path) -> list[str]:
    """ing_03: raw rows and counts against the manifest per hour, and its balance per broker.

    Returns:
        One line per problem; empty when everything balances.
    """
    manifest = read_manifest(folder)
    snapshot_id = manifest["snapshot_id"]
    stored: Counter[str] = Counter()
    counted: Counter[str] = Counter()
    problems = []
    for row in manifest["deliveries"]:
        parts = ("stored", "counted", "merged_cross_broker", "merged_repeat")
        if row["received"] != sum(row[part] for part in parts):
            problems.append(f"unbalanced: {row['hour']} {row['broker']}")
        stored[row["hour"]] += row["stored"]
        counted[row["hour"]] += row["counted"]
    raw_rows = _stored_per_hour(conn, snapshot_id)
    raw_counted = _counted_per_hour(conn, snapshot_id)
    problems += [
        f"stored: {h}" for h in stored.keys() | raw_rows.keys() if stored[h] != raw_rows[h]
    ]
    problems += [
        f"counted: {h}" for h in counted.keys() | raw_counted.keys() if counted[h] != raw_counted[h]
    ]
    return sorted(problems)


def _stored_per_hour(conn: psycopg.Connection, snapshot_id: str) -> Counter[str]:
    rows = conn.execute(
        "select date_trunc('hour', received_at at time zone 'UTC'), count(*) "
        "from raw.mqtt_message where snapshot_id = %s group by 1",
        (snapshot_id,),
    ).fetchall()
    return Counter({f"{hour:%Y-%m-%dT%H}": int(count) for hour, count in rows})


def _counted_per_hour(conn: psycopg.Connection, snapshot_id: str) -> Counter[str]:
    totals: Counter[str] = Counter()
    for table, column in (
        ("mqtt_copy_count", "copy_count"),
        ("mqtt_dropped_count", "delivery_count"),
    ):
        query = sql.SQL(
            "select date_trunc('hour', hour at time zone 'UTC'), sum({}) from {} "
            "where snapshot_id = %s group by 1"
        ).format(sql.Identifier(column), sql.Identifier("raw", table))
        for hour, count in conn.execute(query, (snapshot_id,)).fetchall():
            totals[f"{hour:%Y-%m-%dT%H}"] += int(count)
    return totals


def decode_rates(conn: psycopg.Connection, snapshot_id: str) -> dict[str, float]:
    """ing_04: ok over stored radio deliveries with a payload, per broker flag."""
    rows = conn.execute(
        "select topic, decode_status, seen_public, seen_meshstl, count(*) "
        "from raw.mqtt_message where snapshot_id = %s and not via_mqtt group by 1, 2, 3, 4",
        (snapshot_id,),
    ).fetchall()
    ok: Counter[str] = Counter()
    carried: Counter[str] = Counter()
    for topic, status, seen_public, seen_meshstl, count in rows:
        if topic_class(topic) != "e" or status == "stripped":
            continue
        for flag, seen in (("seen_public", seen_public), ("seen_meshstl", seen_meshstl)):
            if seen:
                carried[flag] += count
                ok[flag] += count if status == "ok" else 0
    return {
        flag: ok[flag] / carried[flag] if carried[flag] else 0.0
        for flag in ("seen_public", "seen_meshstl")
    }


def content_hashes(conn: psycopg.Connection, snapshot_id: str) -> dict[str, str]:
    """Data content hash of each raw table's rows for one snapshot (snp_01)."""
    columns_by_table = {table: allowlist(table) for table in LOAD_ORDER}
    columns_by_table["snapshot"] = list(SNAPSHOT_CONTENT_COLUMNS)
    hashes = {}
    for table, columns in columns_by_table.items():
        query = sql.SQL("select {} from {} where snapshot_id = %s").format(
            sql.SQL(", ").join(sql.Identifier(name) for name in columns),
            sql.Identifier("raw", table),
        )
        total = 0
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cursor:
            for row in cursor.execute(query, (snapshot_id,)):
                total = (total + row_digest(row, columns)) % 2**256
        hashes[table] = f"{total:064x}"
    return hashes


def main() -> None:
    """Load one snapshot by id into the local warehouse: `python -m ingester.load <id>`."""
    from pipeline.common.settings import get_settings

    parser = argparse.ArgumentParser(description="Load a finished snapshot into raw.")
    parser.add_argument("snapshot_id")
    parser.add_argument("--snapshots", type=Path, default=get_ingester_settings().snapshots_dir)
    args = parser.parse_args()
    folder = snapshot_folder(args.snapshots, args.snapshot_id)
    settings = get_settings()
    with psycopg.connect(
        host=settings.warehouse_host,
        port=settings.warehouse_port,
        user=settings.warehouse_user,
        password=settings.warehouse_password.get_secret_value(),
        dbname=settings.warehouse_db,
    ) as conn:
        with conn.transaction():
            inserted = load_snapshot(conn, folder)
        problems = reconcile(conn, folder)
        rates = decode_rates(conn, args.snapshot_id)
    log.info(
        "load_done",
        snapshot_id=args.snapshot_id,
        inserted=inserted,
        problems=problems,
        decode_rates=rates,
    )


if __name__ == "__main__":
    main()
