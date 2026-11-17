"""A throwaway schema holding the raw table shapes, for the tests that write to the warehouse.

Casey, issue #55 comment 6960283869, item 10: the loader tests create their own
throwaway schema and drop it at the end; they never touch `raw` and never
`raw_dev_wh`, so two `make check` runs in different worktrees cannot collide.

Each use gets a schema named `test_load_<12 hex>`, built from
`ingester/sql/raw_tables.sql` through `ingester.load.apply_ddl`, so the shapes
are the warehouse's own. The schema and its tables are created inside a
transaction that is always rolled back, so nothing is ever committed; a guarded
`drop schema if exists` then runs as well, also after a failure, in case a test
committed. Before the rollback the connection must hold no lock on any relation
in `raw` or `raw_dev_wh`: every statement that reads, writes or alters a table
takes one, so this catches a test that reached the real tables through a
default argument. It cannot see a statement made inside a savepoint that was
rolled back, since that releases the savepoint's locks; the tests that use this
fixture open no savepoint.
"""

import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from typing import NamedTuple

import psycopg
import pytest
from psycopg import sql

from ingester import load
from pipeline.common.settings import get_settings

SCHEMA_PREFIX = "test_load_"
THROWAWAY_PATTERN = re.compile(rf"{SCHEMA_PREFIX}[0-9a-f]{{12}}")
REAL_SCHEMAS = ("raw", "raw_dev_wh")


class ThrowawayRaw(NamedTuple):
    """An open connection inside the test's transaction and the schema holding the raw tables."""

    conn: psycopg.Connection
    schema: str


def connect_or_skip() -> psycopg.Connection:
    """A connection to the local warehouse, or a skip where none answers (CI's tests job)."""
    settings = get_settings()
    try:
        return psycopg.connect(
            host=settings.warehouse_host,
            port=settings.warehouse_port,
            user=settings.warehouse_user,
            password=settings.warehouse_password.get_secret_value(),
            dbname=settings.warehouse_db,
            connect_timeout=3,
        )
    except psycopg.OperationalError as err:
        pytest.skip(f"no local warehouse ({err.__class__.__name__}); run make up")


def schema_exists(conn: psycopg.Connection, schema: str) -> bool:
    row = conn.execute("select to_regnamespace(%s) is not null", (schema,)).fetchone()
    return bool(row and row[0])


def locked_relations(conn: psycopg.Connection, schemas: tuple[str, ...]) -> list[str]:
    """Relations in the given schemas that this connection holds a lock on, as schema.name."""
    rows = conn.execute(
        "select n.nspname || '.' || c.relname from pg_locks as l "
        "join pg_class as c on c.oid = l.relation "
        "join pg_namespace as n on n.oid = c.relnamespace "
        "where l.pid = pg_backend_pid() and n.nspname = any(%s) order by 1",
        (list(schemas),),
    ).fetchall()
    return [row[0] for row in rows]


def drop_throwaway(conn: psycopg.Connection, schema: str) -> None:
    """Drop one throwaway schema and commit; any other name is refused before any SQL is sent."""
    if not THROWAWAY_PATTERN.fullmatch(schema):
        raise ValueError(f"{schema!r} is not a throwaway test schema; it is never dropped")
    # Ends whatever the caller left open, so the drop runs in a transaction of its own.
    conn.rollback()
    conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema)))
    conn.commit()


@contextmanager
def throwaway_raw_schema() -> Iterator[ThrowawayRaw]:
    """The raw tables in a new schema, inside a transaction rolled back at exit, then dropped.

    Raises:
        AssertionError: at exit, if the connection touched a table in raw or raw_dev_wh.
    """
    schema = f"{SCHEMA_PREFIX}{secrets.token_hex(6)}"
    with connect_or_skip() as connection:
        try:
            with connection.transaction(force_rollback=True):
                connection.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
                load.apply_ddl(connection, schema=schema)
                yield ThrowawayRaw(connection, schema)
                touched = locked_relations(connection, REAL_SCHEMAS)
                assert touched == [], f"a test touched real tables: {touched}"
        finally:
            if not (connection.closed or connection.broken):
                drop_throwaway(connection, schema)
