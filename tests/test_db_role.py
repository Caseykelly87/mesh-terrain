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
no tables, and the schema check comes first. A statement that names no schema
cannot reach these tables either, since a schema without USAGE drops out of
the search path; the USAGE check below is the proof of that.

Three ways round a grant are checked in the catalog instead of sent: a role
attribute or membership (superuser, `pg_write_all_data`,
`pg_execute_server_program`, the owner), which the attribute test refuses; a
SECURITY DEFINER routine owned by another role, which runs as its owner, so
none may be executable by the test role unless it is on `DEFINER_ALLOWLIST`;
and a default privilege that would grant the role a table created later.

The same no-privilege check covers the real built schemas, `staging`, `core`,
`analytics` and `features`, which the role never needs.

Each form runs in a savepoint inside a transaction that is rolled back, after
the session has shown it is no superuser and holds no USAGE on a watched
schema, and after the role has built its own snapshot table holding one row
in a throwaway schema, so a form that wrongly succeeded would still leave
nothing behind. Skipped where no warehouse answers (CI's tests job); a
warehouse that answers and refuses the role's login fails them instead. CI's
compose smoke test runs them through make check.
"""

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from typing import NamedTuple

import psycopg
import pytest
from psycopg import errors, sql
from raw_schema import REAL_SCHEMAS as WATCHED
from raw_schema import (
    SCHEMA_PREFIX,
    UNCONFIGURED_ROLE,
    RoleForTests,
    connect_or_skip,
    server_refused_login,
    session_credentials,
)

from ingester import load
from pipeline.common.settings import get_settings

TABLE = "snapshot"
OWN_ROW = sql.SQL(
    "insert into {} values ('role-probe', '2026-10-22', 'https://example.invalid', 'none', "
    "'manifest.json', '0', now())"
)
WRITE_PRIVILEGES = "INSERT, UPDATE, DELETE, TRUNCATE"
MERGE = "merge into {table} as t using {own} as s on t.snapshot_id = s.snapshot_id "
# The schemas dbt builds on the target prod, which the role never needs.
REAL_BUILT = ("staging", "core", "analytics", "features")
# SECURITY DEFINER routines owned by another role that the test role may run,
# each reviewed as unable to write a guarded schema. None in PostgreSQL 17,
# PostGIS 3.5 or h3-pg 4.2.
DEFINER_ALLOWLIST: frozenset[str] = frozenset()

# One statement per form. The names are composed by psycopg: {table} is
# <schema>.snapshot, {own} the role's own snapshot table, {schema} the schema,
# {probe} a new table name in it, {database_table} the three-part name and
# {owner} the schema's owner.
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
    "copy from a program": "copy {table} from program 'true'",
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
    "grant itself future tables": (
        "alter default privileges for role {owner} in schema {schema} "
        "grant " + WRITE_PRIVILEGES + " on tables to current_user"
    ),
    "take the schema": "alter schema {schema} owner to current_user",
    "become the owner": "set role {owner}",
    "become the owner by session": "set session authorization {owner}",
}


class RoleSession(NamedTuple):
    """A test role connection inside a transaction rolled back at exit, and its own schema."""

    conn: psycopg.Connection
    own_schema: str


def assert_cannot_reach_a_watched_schema(conn: psycopg.Connection) -> None:
    """Fail before any form is sent if the session is a superuser or sees a watched schema."""
    superuser = conn.execute(
        "select rolsuper from pg_roles where rolname = current_user"
    ).fetchone()
    reachable = conn.execute(
        "select nspname from pg_namespace where nspname = any(%s) "
        "and has_schema_privilege(oid, 'USAGE') order by 1",
        (list(WATCHED),),
    ).fetchall()
    assert superuser == (False,), "the test session is a superuser; no write form is sent"
    assert reachable == [], f"the test session can reach {reachable}; no write form is sent"


@contextmanager
def role_session() -> Iterator[RoleSession]:
    """A rolled-back transaction in which the role owns a snapshot table holding one row."""
    own_schema = f"{SCHEMA_PREFIX}{secrets.token_hex(6)}"
    with connect_or_skip() as conn, conn.transaction(force_rollback=True):
        assert_cannot_reach_a_watched_schema(conn)
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
    )


def send(session: RoleSession, schema: str, template: str) -> None:
    """Run the statement in a savepoint, which an error rolls back before it propagates."""
    with session.conn.transaction():
        session.conn.execute(composed(template, session, schema))


SERVER = 'connection to server at "127.0.0.1", port 5433 failed: '


@pytest.mark.parametrize(
    ("message", "refused"),
    [
        (SERVER + 'FATAL:  password authentication failed for user "mesh_test"', True),
        (SERVER + 'FATAL:  role "test_role_not_configured" does not exist', True),
        (SERVER + 'FATAL:  no pg_hba.conf entry for host "172.18.0.1", user "x"', True),
        # The server answered but is not refusing this login; a later run may pass.
        (SERVER + "FATAL:  the database system is starting up", False),
        # libpq stops before the server can refuse: an empty password is none.
        (SERVER + "fe_sendauth: no password supplied", False),
        (SERVER + "Connection refused", False),
        ("connection timeout expired", False),
    ],
)
def test_a_refused_login_is_told_apart_from_a_server_that_does_not_answer(
    message: str, refused: bool
) -> None:
    assert server_refused_login(psycopg.OperationalError(message)) is refused


@pytest.mark.parametrize(
    "error",
    [errors.InvalidPassword("refused"), errors.InvalidAuthorizationSpecification("refused")],
)
def test_a_refusal_with_an_authentication_sqlstate_counts_whatever_its_text(
    error: psycopg.Error,
) -> None:
    assert server_refused_login(error)


def test_a_refused_login_fails_without_naming_the_role(monkeypatch: pytest.MonkeyPatch) -> None:
    """Casey, issue #55 comment 7017280290, item 2: the values never appear in a transcript."""
    invented = "invented_role_3b8a"

    def refuse(**_: object) -> psycopg.Connection:
        raise psycopg.OperationalError(
            f'failed: FATAL:  password authentication failed for user "{invented}"'
        )

    monkeypatch.setenv("WAREHOUSE_USER", invented)
    get_settings.cache_clear()
    monkeypatch.setattr(psycopg, "connect", refuse)
    try:
        with pytest.raises(pytest.fail.Exception) as failure:
            connect_or_skip()
    finally:
        get_settings.cache_clear()
    assert invented not in str(failure.value)
    # pytest reports a chained exception's text too, and the driver's names the role.
    assert failure.value.__context__ is None
    assert failure.value.__cause__ is None


def test_with_no_test_role_the_tests_log_in_with_a_password_the_server_will_refuse() -> None:
    user, password = session_credentials(None)
    assert user == UNCONFIGURED_ROLE
    assert password != ""


def test_a_configured_test_role_is_used_as_it_is() -> None:
    assert session_credentials(RoleForTests("mesh_test", "secret")) == ("mesh_test", "secret")


def test_the_tests_connect_as_the_test_role() -> None:
    settings = get_settings()
    # Each check is reduced to a bool first, so a failure report shows no role
    # name (Casey, issue #55 comment 7017280290, item 2).
    configured = settings.warehouse_user != UNCONFIGURED_ROLE
    assert configured, (
        "no test role: export WAREHOUSE_TEST_USER and WAREHOUSE_TEST_PASSWORD (README, Test "
        "role), naming a role other than WAREHOUSE_USER, then run make test-role"
    )
    in_use = settings.warehouse_user == settings.warehouse_test_user
    assert in_use, "the tests' WAREHOUSE_USER is not the configured test role"
    with connect_or_skip() as conn:
        row = conn.execute(
            "select current_user = %s and session_user = %s",
            (settings.warehouse_test_user, settings.warehouse_test_user),
        ).fetchone()
    assert row == (True,), "the session is not the configured test role"


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


def test_no_security_definer_routine_of_another_role_is_open_to_the_test_role() -> None:
    with connect_or_skip() as conn:
        rows = conn.execute(
            "select p.oid::regprocedure::text from pg_proc as p "
            "where p.prosecdef "
            "and p.proowner <> (select oid from pg_roles where rolname = current_user) "
            "and has_function_privilege(p.oid, 'EXECUTE') order by 1"
        ).fetchall()
    assert {row[0] for row in rows} - DEFINER_ALLOWLIST == set()


def test_no_default_privilege_reaches_the_test_role_in_a_guarded_schema() -> None:
    """A default privilege, per schema or for every schema, would grant a table made later."""
    with connect_or_skip() as conn:
        rows = conn.execute(
            "select coalesce(n.nspname, '<every schema>'), d.defaclobjtype::text, "
            "acl.privilege_type "
            "from pg_default_acl as d "
            "left join pg_namespace as n on n.oid = d.defaclnamespace, "
            "aclexplode(d.defaclacl) as acl "
            "where (d.defaclnamespace = 0 or n.nspname = any(%s)) "
            "and (acl.grantee = 0 "
            "or acl.grantee = (select oid from pg_roles where rolname = current_user)) "
            "order by 1, 2, 3",
            ([*WATCHED, *REAL_BUILT],),
        ).fetchall()
    assert rows == []


@pytest.mark.parametrize("schema", [*WATCHED, *REAL_BUILT])
def test_the_test_role_holds_no_privilege_on_a_raw_or_real_schema(schema: str) -> None:
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
        owned_by_the_role = owner == conn.info.user
    assert not owned_by_the_role, f"the test role owns {schema}"
    assert held == (False, False)
    assert writable == []


@pytest.mark.parametrize("form", sorted(REFUSED))
@pytest.mark.parametrize("schema", WATCHED)
def test_a_write_to_a_real_raw_schema_is_refused(schema: str, form: str) -> None:
    with role_session() as session, pytest.raises(errors.InsufficientPrivilege):
        send(session, schema, REFUSED[form])


@pytest.mark.parametrize("schema", WATCHED)
def test_a_copy_into_a_real_raw_schema_is_refused(schema: str) -> None:
    statement = sql.SQL("copy {} (snapshot_id) from stdin").format(sql.Identifier(schema, TABLE))
    with role_session() as session, pytest.raises(errors.InsufficientPrivilege):
        with session.conn.transaction(), session.conn.cursor() as cursor:
            with cursor.copy(statement) as copy:
                copy.write_row(("role-probe",))
