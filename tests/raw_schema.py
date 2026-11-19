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
in `raw`, `raw_dev_wh` or `raw_dev_ta`: every statement that reads, writes or alters a table
takes one, so this catches a test that reached the real tables through a
default argument. It cannot see a statement made inside a savepoint that was
rolled back, since that releases the savepoint's locks; the tests that use this
fixture open no savepoint.

Every connection here, and every other one the tests open, is made as the
test role (`tests/conftest.py`), which has no privilege on `raw`, `raw_dev_wh`
or `raw_dev_ta` (`docker/warehouse/test-role.sql`), so a write that gets past
both checks above still fails (`tests/test_db_role.py`).
"""

import os
import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from typing import NamedTuple

import psycopg
import pytest
import report_redaction
from psycopg import sql
from pydantic import ValidationError

from ingester import load
from pipeline.common.settings import get_settings

SCHEMA_PREFIX = "test_load_"
THROWAWAY_PATTERN = re.compile(rf"{SCHEMA_PREFIX}[0-9a-f]{{12}}")
REAL_SCHEMAS = ("raw", "raw_dev_wh", "raw_dev_ta")

# The placeholders tests/conftest.py puts in the environment for every session,
# and the login connect_or_skip uses when no test role is configured, so the
# server refuses it, and the test fails, instead of falling back to the owner.
# The password is not empty because libpq treats an empty one as none and stops
# before the server can refuse.
UNCONFIGURED_ROLE = "test_role_not_configured"
UNCONFIGURED_PASSWORD = "test-role-not-configured"
# SQLSTATE invalid_password and invalid_authorization_specification.
AUTHENTICATION_SQLSTATES = frozenset({"28P01", "28000"})
# The server's own messages for those two codes, for the errors psycopg raises
# without a SQLSTATE, as it does for every failed connection: libpq gives it no
# server result there. They are English, as this image's server writes them.
AUTHENTICATION_MESSAGES = (
    "FATAL:  password authentication failed",
    "FATAL:  role ",
    "FATAL:  no pg_hba.conf entry",
)


class RoleForTests(NamedTuple):
    """The name and password of the role every test connects as."""

    user: str
    password: str


def role_for_tests() -> RoleForTests | None:
    """The configured test role, or None when it is unset or names the owner.

    tests/conftest.py reads it once, before it puts placeholders in the
    environment, so warehouse_user is still the owner here.
    """
    try:
        settings = get_settings()
    except ValidationError:
        return None
    user, password = settings.warehouse_test_user, settings.warehouse_test_password
    if not user or password is None or user == settings.warehouse_user:
        return None
    return RoleForTests(user, password.get_secret_value())


class ThrowawayRaw(NamedTuple):
    """An open connection inside the test's transaction and the schema holding the raw tables."""

    conn: psycopg.Connection
    schema: str


def session_credentials(role: RoleForTests | None) -> tuple[str, str]:
    """The user and password the tests log in with: the test role, or one the server refuses."""
    if role is None:
        return UNCONFIGURED_ROLE, UNCONFIGURED_PASSWORD
    return role.user, role.password


# The configured test role. tests/conftest.py sets it once, gives the same two
# values to tests/report_redaction.py for scrubbing, and puts placeholders in
# the environment, so a test that copies os.environ holds no test role value
# (Casey, issue #55 comment 7017280290, item 2). Only the modules in conftest's
# ROLE_IN_ENVIRONMENT get the values in the environment, through
# role_environment, while they run.
_role: list[RoleForTests] = []
ROLE_VARIABLES = ("WAREHOUSE_USER", "WAREHOUSE_PASSWORD")
TEST_ROLE_VARIABLES = ("WAREHOUSE_TEST_USER", "WAREHOUSE_TEST_PASSWORD")


def use_role(role: RoleForTests | None = None) -> None:
    """Hold the configured test role for connect_or_skip and role_environment; None for none."""
    _role[:] = [role] if role else []


def role_configured() -> bool:
    return bool(_role)


def placeholder_environment(patch: pytest.MonkeyPatch) -> None:
    """Put placeholders in all four variables, so the environment holds no test role value.

    The test role's own two variables get them too, since the settings would
    otherwise read the real ones from the checkout's .env.
    """
    for name in (*ROLE_VARIABLES, *TEST_ROLE_VARIABLES):
        patch.setenv(
            name, UNCONFIGURED_PASSWORD if name.endswith("PASSWORD") else UNCONFIGURED_ROLE
        )


def role_environment(patch: pytest.MonkeyPatch) -> None:
    """Put the test role in WAREHOUSE_USER and WAREHOUSE_PASSWORD, for a dbt that connects."""
    for name, value in zip(
        ROLE_VARIABLES, session_credentials(_role[0] if _role else None), strict=True
    ):
        patch.setenv(name, value)


def environment_holds_the_role() -> bool:
    """Whether any environment variable holds either test role value."""
    return any(
        value in entry for value in (_role[0] if _role else ()) for entry in os.environ.values()
    )


def redaction_covers_the_role() -> bool:
    """Whether pytest reports are still scrubbed of both test role values."""
    return not _role or report_redaction.covers(*_role[0])


def session_is_the_test_role(conn: psycopg.Connection) -> bool:
    """Whether the connection's current and session user are the configured test role."""
    user = _role[0].user if _role else UNCONFIGURED_ROLE
    row = conn.execute("select current_user = %s and session_user = %s", (user, user)).fetchone()
    return row == (True,)


def _connection_keywords() -> dict[str, object]:
    """Where and as whom connect_or_skip connects; the role comes from _role, not os.environ."""
    settings = get_settings()
    user, password = session_credentials(_role[0] if _role else None)
    return {
        "host": settings.warehouse_host,
        "port": settings.warehouse_port,
        "user": user,
        "password": password,
        "dbname": settings.warehouse_db,
    }


def server_refused_login(err: psycopg.Error) -> bool:
    """Whether the server answered and refused the login, rather than not answering.

    A server that is still starting up, or that does not answer, is not a
    refusal: the test skips, as where no warehouse runs.
    """
    if err.sqlstate is not None:
        return err.sqlstate in AUTHENTICATION_SQLSTATES
    text = str(err)
    return any(message in text for message in AUTHENTICATION_MESSAGES)


def connect_or_skip() -> psycopg.Connection:
    """A connection to the local warehouse, or a skip where none answers (CI's tests job).

    A warehouse that answers and refuses the test role's login fails the test
    instead: the role is missing, its password differs, or no test role is
    configured, and a run that skipped every warehouse test would pass.
    """
    try:
        return psycopg.connect(**_connection_keywords(), connect_timeout=3)
    except psycopg.OperationalError as err:
        refused, kind = server_refused_login(err), err.__class__.__name__
    # Outside the handler, so the report chains no driver message, whose text
    # names the role: neither value appears in a transcript or log (Casey,
    # issue #55 comment 7017280290, item 2).
    if refused:
        pytest.fail(
            "the warehouse refused the test role's login: export WAREHOUSE_TEST_USER "
            "and WAREHOUSE_TEST_PASSWORD (README, Test role), then run make test-role",
            pytrace=False,
        )
    pytest.skip(f"no local warehouse ({kind}); run make up")


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
        AssertionError: at exit, if the connection touched a table in raw, raw_dev_wh or raw_dev_ta.
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
