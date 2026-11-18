"""The test role cannot write raw, raw_dev_wh or raw_dev_ta, however the write is written.

Casey, issue #55 comment 6961809084, item 10 (verbatim): "tests connect as a
database role with no INSERT, UPDATE, DELETE or TRUNCATE on raw or raw_dev_wh,
so any write fails however it is written, with a test that proves the
refusal." PR #64 comment 6968763718, item 5 (verbatim): "the test database role
refuses writes on raw_dev_ta as well as raw and raw_dev_wh."

`docker/warehouse/test-role.sql` gives the role no privilege at all on the
three schemas, not even USAGE, and makes it a member of no role. A privilege
check runs on the schema's and the table's identity after the name is
resolved, so the spelling of a statement cannot get past it; what can differ
between forms is only whether a statement reaches the check, which is why each
form below is sent for real. Every form must fail with "permission denied",
whether or not the table it names exists: `raw_dev_wh` and `raw_dev_ta` hold
no tables, and the schema check comes first. Two forms do not name the schema
in the statement, a search_path and `set schema`; a schema without USAGE drops
out of the search path, so they fail with "relation does not exist".

Each form runs in a savepoint inside a transaction that is rolled back, after
the role has built its own snapshot table holding one row in a throwaway
schema, so a form that wrongly succeeded would still leave nothing behind.
Skipped where no warehouse answers (CI's tests job); CI's compose smoke test
runs them through make check.
"""

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from typing import NamedTuple

import psycopg
import pytest
from psycopg import errors, sql
from raw_schema import REAL_SCHEMAS as WATCHED
from raw_schema import SCHEMA_PREFIX, UNCONFIGURED_ROLE, connect_or_skip

from ingester import load
from pipeline.common.settings import get_settings

TABLE = "snapshot"
OWN_ROW = sql.SQL(
    "insert into {} values ('role-probe', '2026-10-22', 'https://example.invalid', 'none', "
    "'manifest.json', '0', now())"
)
WRITE_PRIVILEGES = "INSERT, UPDATE, DELETE, TRUNCATE"
MERGE = "merge into {table} as t using {own} as s on t.snapshot_id = s.snapshot_id "

# One statement or a sequence of them per form. The names are composed by
# psycopg: {table} is <schema>.snapshot, {own} the role's own snapshot table,
# {schema} the schema, {probe} a new table name in it, {database_table} the
# three-part name, {owner} the schema's owner and {name} the schema as a string.
REFUSED = {
    "insert values": (
        "insert into {table} (snapshot_id, capture_date, source_url, licence, manifest_path, "
        "manifest_sha256, registered_at) values ('role-probe', '2026-10-22', 'x', 'x', 'x', "
        "'x', now())"
    ),
    "insert select": "insert into {table} select * from {own}",
    "insert on conflict": "insert into {table} select * from {own} on conflict do nothing",
    "insert in a writable cte": (
        "with moved as (insert into {table} select * from {own} returning 1) "
        "select count(*) from moved"
    ),
    "insert by three-part name": "insert into {database_table} select * from {own}",
    "merge insert": MERGE + "when not matched then insert values (s.snapshot_id, "
    "s.capture_date, s.source_url, s.licence, s.manifest_path, s.manifest_sha256, "
    "s.registered_at)",
    "update": "update {table} set licence = 'x'",
    "update from": (
        "update {table} as t set licence = s.licence from {own} as s "
        "where t.snapshot_id = s.snapshot_id"
    ),
    "merge update": MERGE + "when matched then update set licence = s.licence",
    "select for update": "select 1 from {table} for update",
    "delete": "delete from {table}",
    "delete using": "delete from {table} as t using {own} as s where t.snapshot_id = s.snapshot_id",
    "merge delete": MERGE + "when matched then delete",
    "truncate": "truncate {table}",
    "truncate only cascade": "truncate only {table} cascade",
    "lock for writing": "lock table {table} in row exclusive mode",
    "create a table": "create table {probe} (id integer)",
    "create a table as": "create table {probe} as select * from {own}",
    "select into": "select * into {probe} from {own}",
    "create a view": "create view {probe} as select 1 as id",
    "move its own table in": "alter table {own} set schema {schema}",
    "rename a table": "alter table {table} rename to probe",
    "drop a table": "drop table {table}",
    "grant itself the table": "grant " + WRITE_PRIVILEGES + " on {table} to current_user",
    "grant itself the schema": "grant usage, create on schema {schema} to current_user",
    "take the schema": "alter schema {schema} owner to current_user",
    "become the owner": "set role {owner}",
    "become the owner by session": "set session authorization {owner}",
}
UNRESOLVED = {
    "unqualified after search_path": (
        "set local search_path to {schema}",
        "insert into " + TABLE + " select * from {own}",
    ),
    "unqualified after set schema": (
        "set local schema {name}",
        "delete from " + TABLE,
    ),
}


class RoleSession(NamedTuple):
    """A test role connection inside a transaction rolled back at exit, and its own schema."""

    conn: psycopg.Connection
    own_schema: str


@contextmanager
def role_session() -> Iterator[RoleSession]:
    """A rolled-back transaction in which the role owns a snapshot table holding one row."""
    own_schema = f"{SCHEMA_PREFIX}{secrets.token_hex(6)}"
    with connect_or_skip() as conn, conn.transaction(force_rollback=True):
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(own_schema)))
        load.apply_ddl(conn, schema=own_schema)
        conn.execute(OWN_ROW.format(sql.Identifier(own_schema, TABLE)))
        yield RoleSession(conn, own_schema)


def schema_owner(conn: psycopg.Connection, schema: str) -> str:
    row = conn.execute(
        "select pg_get_userbyid(nspowner) from pg_namespace where nspname = %s", (schema,)
    ).fetchone()
    assert row is not None, f"schema {schema} is missing; run make test-role"
    return str(row[0])


def composed(template: str, session: RoleSession, schema: str) -> sql.Composed:
    """One form's statement with every name in it quoted by psycopg."""
    database = session.conn.info.dbname
    return sql.SQL(template).format(
        table=sql.Identifier(schema, TABLE),
        own=sql.Identifier(session.own_schema, TABLE),
        schema=sql.Identifier(schema),
        probe=sql.Identifier(schema, "probe"),
        database_table=sql.Identifier(database, schema, TABLE),
        owner=sql.Identifier(schema_owner(session.conn, schema)),
        name=sql.Literal(schema),
    )


def send(session: RoleSession, schema: str, statements: tuple[str, ...]) -> None:
    """Run the statements in a savepoint, which an error rolls back before it propagates."""
    with session.conn.transaction():
        for template in statements:
            session.conn.execute(composed(template, session, schema))


def test_the_tests_connect_as_the_test_role() -> None:
    settings = get_settings()
    assert settings.warehouse_user != UNCONFIGURED_ROLE, (
        "no test role: set WAREHOUSE_TEST_USER and WAREHOUSE_TEST_PASSWORD in .env as "
        ".env.example does, naming a role other than WAREHOUSE_USER, then run make test-role"
    )
    assert settings.warehouse_user == settings.warehouse_test_user
    with connect_or_skip() as conn:
        row = conn.execute("select current_user, session_user").fetchone()
    assert row == (settings.warehouse_test_user, settings.warehouse_test_user)


def test_the_test_role_has_no_attribute_or_membership_that_passes_a_grant() -> None:
    with connect_or_skip() as conn:
        attributes = conn.execute(
            "select rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls "
            "from pg_roles where rolname = current_user"
        ).fetchone()
        parents = conn.execute(
            "select pg_get_userbyid(roleid) from pg_auth_members "
            "where member = (select oid from pg_roles where rolname = current_user)"
        ).fetchall()
    assert attributes == (False, False, False, False, False)
    assert parents == []


@pytest.mark.parametrize("schema", WATCHED)
def test_the_test_role_holds_no_privilege_on_a_real_raw_schema(schema: str) -> None:
    with connect_or_skip() as conn:
        owner = schema_owner(conn, schema)
        held = conn.execute(
            "select has_schema_privilege(%s, 'USAGE'), has_schema_privilege(%s, 'CREATE')",
            (schema, schema),
        ).fetchone()
        writable = conn.execute(
            "select c.relname from pg_class as c "
            "where c.relnamespace = to_regnamespace(%s) and c.relkind in ('r', 'p', 'v', 'm', 'f') "
            "and (has_table_privilege(c.oid, %s) "
            "or has_any_column_privilege(c.oid, 'INSERT, UPDATE')) order by 1",
            (schema, WRITE_PRIVILEGES),
        ).fetchall()
        user = conn.info.user
    assert owner != user
    assert held == (False, False)
    assert writable == []


@pytest.mark.parametrize("form", sorted(REFUSED))
@pytest.mark.parametrize("schema", WATCHED)
def test_a_write_to_a_real_raw_schema_is_refused(schema: str, form: str) -> None:
    with role_session() as session, pytest.raises(errors.InsufficientPrivilege):
        send(session, schema, (REFUSED[form],))


@pytest.mark.parametrize("form", sorted(UNRESOLVED))
@pytest.mark.parametrize("schema", WATCHED)
def test_a_write_by_an_unqualified_name_finds_no_raw_table(schema: str, form: str) -> None:
    with role_session() as session, pytest.raises(errors.UndefinedTable):
        send(session, schema, UNRESOLVED[form])


@pytest.mark.parametrize("schema", WATCHED)
def test_a_copy_into_a_real_raw_schema_is_refused(schema: str) -> None:
    statement = sql.SQL("copy {} (snapshot_id) from stdin").format(sql.Identifier(schema, TABLE))
    with role_session() as session, pytest.raises(errors.InsufficientPrivilege):
        with session.conn.transaction(), session.conn.cursor() as cursor:
            with cursor.copy(statement) as copy:
                copy.write_row(("role-probe",))
