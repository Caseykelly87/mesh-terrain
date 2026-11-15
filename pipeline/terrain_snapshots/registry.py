"""Registering terrain snapshots in raw.snapshot (snp_01), and reading a registration back.

`python -m pipeline.terrain_snapshots register <id>` opens the warehouse
named by the pipeline settings, creates the raw tables only when
raw.snapshot is missing (with ingester.load.apply_ddl, whose statements are
`create table if not exists`), and calls register_verified, which checks
every file against the manifest before inserting the row and inserts nothing
for an id already registered. The lead's checklist for each snapshot is:
write, then register.

The DSM command reads one row back: the OSM snapshot it takes water from
must be registered with the manifest_sha256 of the folder it reads (Casey,
unit 5b: "OpenStreetMap water polygons from the registered snapshot"). Only
`register` and that check touch the database; the other commands take no
connection.
"""

from pathlib import Path

import psycopg

from ingester.load import apply_ddl
from pipeline.common.settings import Settings, get_settings
from pipeline.common.snapshot import register_verified, snapshot_folder
from pipeline.terrain_snapshots.common import timed

CONNECT_TIMEOUT_S = 10


def connect(settings: Settings | None = None) -> psycopg.Connection:
    """A connection to the warehouse the pipeline settings name."""
    settings = settings or get_settings()
    return psycopg.connect(
        host=settings.warehouse_host,
        port=settings.warehouse_port,
        user=settings.warehouse_user,
        password=settings.warehouse_password.get_secret_value(),
        dbname=settings.warehouse_db,
        connect_timeout=CONNECT_TIMEOUT_S,
    )


def raw_snapshot_exists(conn: psycopg.Connection) -> bool:
    """Whether the table raw.snapshot exists."""
    row = conn.execute("select to_regclass('raw.snapshot') is not null").fetchone()
    return bool(row and row[0])


def register_snapshot(conn: psycopg.Connection, root: Path, snapshot_id: str) -> int:
    """Register one finished, verified snapshot, creating raw.snapshot first if it is missing.

    Args:
        conn: An open warehouse connection; the work runs in one transaction.
        root: The snapshots folder.
        snapshot_id: The finished snapshot's id.

    Returns:
        1 when the row is new, 0 when the id was already registered.

    Raises:
        SnapshotError: for a missing, partial or changed snapshot, or a
            manifest register refuses.
    """
    with timed("snapshot_register_command", snapshot_id=snapshot_id) as details:
        folder = snapshot_folder(root, snapshot_id)
        with conn.transaction():
            created = not raw_snapshot_exists(conn)
            if created:
                apply_ddl(conn)
            inserted = register_verified(conn, folder)
        details.update(inserted=inserted, created_raw_tables=created)
    return inserted


def registered_manifest_sha256(conn: psycopg.Connection, snapshot_id: str) -> str | None:
    """The manifest_sha256 raw.snapshot holds for an id, or None when it has no row."""
    if not raw_snapshot_exists(conn):
        return None
    row = conn.execute(
        "select manifest_sha256 from raw.snapshot where snapshot_id = %s", (snapshot_id,)
    ).fetchone()
    return None if row is None else str(row[0])


def lookup_registered(snapshot_id: str) -> str | None:
    """registered_manifest_sha256 over a connection of its own, from the pipeline settings."""
    with connect() as conn:
        return registered_manifest_sha256(conn, snapshot_id)
