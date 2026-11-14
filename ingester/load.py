"""Load a finished snapshot folder into the raw schema (ing_01, ing_02, ing_05, ing_07, snp_01).

The loader refuses a `.partial` folder and a folder without a manifest, checks
every file against its sha256 in the manifest, applies the idempotent raw DDL,
refuses when a raw table's columns differ from the allowlist, registers the
snapshot in raw.snapshot and copies each table through a temporary table
with `on conflict do nothing` on the contract keys. Rows are only ever
inserted, so a second load adds nothing. The caller owns the transaction.

Checks, run by the asset and by the load command: reconcile (ing_03, raw
against the manifest's deliveries per hour and broker), decode_rates (ing_04)
and hash_problems (snp_01: each raw table's data content hash against the
manifest's). The command exits non-zero when reconcile or the hashes find a
problem.

What reconcile can split by broker: the stored rows, since each carries both
broker flags. The count tables carry no broker (their keys are the contract's),
so counted deliveries are checked per hour only.
"""

import argparse
import struct
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


BALANCE_PARTS = ("stored", "counted", "merged_cross_broker", "merged_repeat")


def reconcile(conn: psycopg.Connection, folder: Path) -> list[str]:
    """ing_03: raw against the manifest's deliveries per hour and broker.

    Checks, each line of the result naming what failed and where:
    `unbalanced: hour broker` when received differs from stored plus counted
    plus both merged counts; `flagged: hour broker` when the raw rows of the
    hour carrying the broker's flag differ from rows_with_flag;
    `cross_broker: broker` when the raw rows carrying the broker's flag differ
    from its stored plus merged_cross_broker deliveries over the snapshot;
    `stored: hour` and `counted: hour` when raw rows or counts differ from the
    manifest's sums over both brokers.

    Returns:
        One line per problem, sorted; empty when everything balances.
    """
    manifest = read_manifest(folder)
    rows = manifest["deliveries"]
    flagged = _flagged_per_hour(conn, manifest["snapshot_id"])
    problems = [
        f"unbalanced: {row['hour']} {row['broker']}"
        for row in rows
        if row["received"] != sum(row[part] for part in BALANCE_PARTS)
    ]
    problems += _differences("flagged", _manifest_sum(rows, "rows_with_flag", True), flagged)
    problems += _differences("cross_broker", _first_and_cross(rows), _per_broker(flagged))
    stored = _stored_per_hour(conn, manifest["snapshot_id"])
    problems += _differences("stored", _manifest_sum(rows, "stored", False), stored)
    counted = _counted_per_hour(conn, manifest["snapshot_id"])
    problems += _differences("counted", _manifest_sum(rows, "counted", False), counted)
    return sorted(problems)


def _manifest_sum(rows: list[dict[str, Any]], field: str, by_broker: bool) -> Counter[str]:
    totals: Counter[str] = Counter()
    for row in rows:
        totals[f"{row['hour']} {row['broker']}" if by_broker else row["hour"]] += row[field]
    return totals


def _first_and_cross(rows: list[dict[str, Any]]) -> Counter[str]:
    """Per broker, its first sightings plus the rows it joined after the other broker."""
    totals: Counter[str] = Counter()
    for row in rows:
        totals[row["broker"]] += row["stored"] + row["merged_cross_broker"]
    return totals


def _per_broker(per_hour: Counter[str]) -> Counter[str]:
    totals: Counter[str] = Counter()
    for key, count in per_hour.items():
        totals[key.split(" ")[1]] += count
    return totals


def _differences(name: str, expected: Counter[str], found: Counter[str]) -> list[str]:
    return [
        f"{name}: {key}" for key in expected.keys() | found.keys() if expected[key] != found[key]
    ]


def _stored_per_hour(conn: psycopg.Connection, snapshot_id: str) -> Counter[str]:
    rows = conn.execute(
        "select date_trunc('hour', received_at at time zone 'UTC'), count(*) "
        "from raw.mqtt_message where snapshot_id = %s group by 1",
        (snapshot_id,),
    ).fetchall()
    return Counter({f"{hour:%Y-%m-%dT%H}": int(count) for hour, count in rows})


def _flagged_per_hour(conn: psycopg.Connection, snapshot_id: str) -> Counter[str]:
    """Raw rows carrying each broker's flag, by "hour broker"."""
    rows = conn.execute(
        "select date_trunc('hour', received_at at time zone 'UTC'), "
        "count(*) filter (where seen_public), count(*) filter (where seen_meshstl) "
        "from raw.mqtt_message where snapshot_id = %s group by 1",
        (snapshot_id,),
    ).fetchall()
    found: Counter[str] = Counter()
    for hour, public, meshstl in rows:
        found[f"{hour:%Y-%m-%dT%H} public"] += int(public)
        found[f"{hour:%Y-%m-%dT%H} meshstl"] += int(meshstl)
    return +found


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


def zero_positions(conn: psycopg.Connection, snapshot_id: str) -> dict[str, int]:
    """pos_03: rows with latitude_i and longitude_i both exactly 0, per broker flag and source.

    A position the sender set to 0, 0 is stored as sent (the decoder keeps a set
    field); a missing coordinate is null and is never read as 0 (Casey, 2026-11-14).
    """
    rows = conn.execute(
        "select seen_public, seen_meshstl, position_source, count(*) from raw.mqtt_message "
        "where snapshot_id = %s and latitude_i = 0 and longitude_i = 0 group by 1, 2, 3",
        (snapshot_id,),
    ).fetchall()
    found: Counter[str] = Counter()
    for seen_public, seen_meshstl, source, count in rows:
        for flag, seen in (("seen_public", seen_public), ("seen_meshstl", seen_meshstl)):
            if seen:
                found[f"{flag}, {source}"] += int(count)
    return dict(found)


def warn_zero_positions(snapshot_id: str, counts: dict[str, int]) -> None:
    """Log rows at 0, 0 (pos_03) at warning; they do not fail a load of a finished snapshot."""
    if counts:
        log.warning("pos_03_positions_at_zero", snapshot_id=snapshot_id, rows=counts)


def _float32(value: Any) -> Any:
    """A float4 as the float32 the Parquet file held; PostgreSQL sends the shortest text."""
    if isinstance(value, list):
        return [_float32(item) for item in value]
    return None if value is None else struct.unpack("<f", struct.pack("<f", value))[0]


def content_hashes(conn: psycopg.Connection, snapshot_id: str) -> dict[str, str]:
    """Data content hash of each raw table's rows for one snapshot (snp_01).

    The three landed tables hash as their Parquet files do (ingester.parquet),
    so they compare with the manifest; raw.snapshot leaves out registered_at,
    which differs on every load.
    """
    columns_by_table = {table: allowlist(table) for table in LOAD_ORDER}
    columns_by_table["snapshot"] = list(SNAPSHOT_CONTENT_COLUMNS)
    hashes = {}
    for table, columns in columns_by_table.items():
        real = [c.name for c in TABLES.get(table, ()) if c.sql_type in {"real", "real[]"}]
        query = sql.SQL("select {} from {} where snapshot_id = %s").format(
            sql.SQL(", ").join(sql.Identifier(name) for name in columns),
            sql.Identifier("raw", table),
        )
        total = 0
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cursor:
            for row in cursor.execute(query, (snapshot_id,)):
                as_parquet = {**row, **{name: _float32(row[name]) for name in real}}
                total = (total + row_digest(as_parquet, columns)) % 2**256
        hashes[table] = f"{total:064x}"
    return hashes


def hash_problems(conn: psycopg.Connection, folder: Path) -> list[str]:
    """snp_01: one `content_hash: table` line per raw table whose hash differs from the manifest."""
    manifest = read_manifest(folder)
    found = content_hashes(conn, manifest["snapshot_id"])
    expected = manifest["content_hashes"]
    return [f"content_hash: {table}" for table in LOAD_ORDER if found[table] != expected[table]]


def report(
    snapshot_id: str, inserted: dict[str, int], problems: list[str], rates: dict[str, float]
) -> int:
    """Log the load's outcome; 1 when there is any problem, else 0."""
    if problems:
        log.error("load_problems", snapshot_id=snapshot_id, inserted=inserted, problems=problems)
        return 1
    log.info("load_done", snapshot_id=snapshot_id, inserted=inserted, decode_rates=rates)
    return 0


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
        problems = reconcile(conn, folder) + hash_problems(conn, folder)
        rates = decode_rates(conn, args.snapshot_id)
        warn_zero_positions(args.snapshot_id, zero_positions(conn, args.snapshot_id))
    raise SystemExit(report(args.snapshot_id, inserted, problems, rates))


if __name__ == "__main__":
    main()
