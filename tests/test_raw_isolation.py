"""No test sends SQL to the real raw tables, and the throwaway schema is gone after each use.

Casey, issue #55 comment 6960283869, item 10: the loader tests use their own
throwaway schema, never `raw` and never `raw_dev_wh`. The source scan reads
every test module; the database tests need the local stack and skip without it.

What the scan matches, outside docstrings: a string literal or f-string whose
text names `raw.` or `raw_dev_wh.` (quoted or not, f-string values read as
`x`); a string literal that is exactly `raw` or `raw_dev_wh`, however it is
used afterwards (a variable, `.format`, `%`, `+`, an Identifier); and any
name, attribute or import called `RAW_SCHEMA`. Each match is reported with
the function (or `<module>`) it sits in, and ALLOWED lists the ones that are
not SQL. PROBES holds one case per form the PR #61 review found the first
scan missed (`schema=load.RAW_SCHEMA`, `schema=RAW_SCHEMA`, `.format("raw")`,
`% "raw"`, concatenation, a variable holding `raw.snapshot` or `raw` used in
an f-string, a bare `Identifier("raw", ...)`, `sql.Identifier(S, ...)` with
`S = "raw"`) and each is caught now.

It does not see a name built at run time (`"r" + "aw"`, a join, a value read
from settings, the environment or a file, `getattr`), a test in another
language, nor a production function called without `schema=`, which falls
back to `raw`; the throwaway fixture's lock check catches that last case, on
its own connection only. A database role with no write on `raw` or
`raw_dev_wh` for the tests is queued for platform-eng (Casey, issue #55
comment 6961809084, item 10); it refuses a write however it is written.
"""

import ast
import re
import secrets
from collections import defaultdict
from pathlib import Path

import psycopg
import pytest
import raw_schema
from psycopg import sql
from raw_schema import REAL_SCHEMAS as WATCHED
from raw_schema import (
    SCHEMA_PREFIX,
    connect_or_skip,
    drop_throwaway,
    locked_relations,
    schema_exists,
    throwaway_raw_schema,
)

TESTS = Path(__file__).resolve().parent
QUALIFIED = re.compile(rf'\b({"|".join(WATCHED)})"?\.["\w]')
NAMES = frozenset({"RAW_SCHEMA"})
REAL_TARGET_TEST = "test_the_real_target_builds_the_real_schemas_and_reads_the_real_raw"
# Matches that are not SQL, by (file, enclosing function or <module>), with
# every match expected there; any other match, or a different set, fails.
ALLOWED: dict[tuple[str, str], list[str]] = {
    # The two names the scan and the lock check look for.
    ("raw_schema.py", "<module>"): ["constant 'raw'", "constant 'raw_dev_wh'"],
    # A Dagster asset key and an S3 bucket name, not schemas.
    ("test_assets.py", "test_the_raw_snapshot_asset_and_its_checks_are_defined"): [
        "constant 'raw'"
    ],
    ("test_settings.py", "<module>"): ["constant 'raw'"],
    # warehouse-eng's dbt target checks: schema names in dbt's manifest, and a
    # text its own SQL scanner reads; none is sent to the database.
    ("test_dbt_targets.py", "<module>"): ["constant 'raw'"],
    (
        "test_dbt_targets.py",
        "test_the_real_target_builds_the_real_schemas_and_reads_the_real_raw",
    ): [
        "constant 'raw'",
        "constant 'raw'",
    ],
    ("test_dbt_targets.py", "test_the_scanner_finds_schema_names_written_into_sql"): [
        "qualified raw",
        "constant 'raw'",
    ],
    # The production default, checked on signatures, rendered text and log fields only.
    ("test_load.py", "test_every_loader_query_takes_a_schema_that_defaults_to_raw"): [
        "constant 'raw'"
    ],
    ("test_common_snapshot.py", "test_registration_takes_a_schema_that_defaults_to_raw"): [
        "constant 'raw'"
    ],
    ("test_common_snapshot.py", "test_registration_logs_its_start_and_the_rows_inserted"): [
        "constant 'raw'"
    ],
    ("test_common_snapshot.py", "test_register_inserts_into_the_schema_it_is_given"): [
        "qualified raw"
    ],
}


def docstrings(tree: ast.Module) -> set[int]:
    """The ids of the string nodes that are docstrings, which describe rather than run SQL."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


class _Scan(ast.NodeVisitor):
    """Collects (scope, kind, line) for every match, scope being the innermost def or class."""

    def __init__(self, skip: set[int]) -> None:
        self.skip = skip
        self.scopes = ["<module>"]
        self.hits: list[tuple[str, str, int]] = []

    def _hit(self, node: ast.AST, kind: str) -> None:
        self.hits.append((self.scopes[-1], kind, getattr(node, "lineno", 0)))

    def _scoped(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self.scopes.append(node.name)
        self.generic_visit(node)
        self.scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._scoped(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._scoped(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scoped(node)

    def _text(self, node: ast.AST, text: str) -> None:
        if match := QUALIFIED.search(text):
            self._hit(node, f"qualified {match.group(1)}")

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        parts = [part.value if isinstance(part, ast.Constant) else "x" for part in node.values]
        self._text(node, "".join(str(part) for part in parts))
        for part in node.values:
            if isinstance(part, ast.FormattedValue):
                self.visit(part)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and id(node) not in self.skip:
            if node.value in WATCHED:
                self._hit(node, f"constant {node.value!r}")
            else:
                self._text(node, node.value)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in NAMES:
            self._hit(node, f"name {node.id}")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in NAMES:
            self._hit(node, f"attribute {node.attr}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name in NAMES or alias.asname in NAMES:
                self._hit(node, f"import {alias.name}")


def real_schema_uses(source: str) -> list[tuple[str, str, int]]:
    """Every (scope, kind, line) in the source that may name raw or raw_dev_wh."""
    tree = ast.parse(source)
    scan = _Scan(docstrings(tree))
    scan.visit(tree)
    return scan.hits


# One per form a test could use to reach a real schema; @ stands for its name.
PROBES = {
    "qualified literal": 'conn.execute("update @.mqtt_message set rx_snr = 0")',
    "quoted identifiers": 'conn.execute(\'select 1 from "@"."snapshot"\')',
    "format": 'q = "select * from {}.snapshot".format("@")',
    "percent": 'q = "select * from %s.snapshot" % "@"',
    "concatenation": 'q = "select * from " + "@" + ".snapshot"',
    "qualified concatenation": 'q = "select * from " + "@.snapshot"',
    "table variable in f-string": 'table = "@.snapshot"\nq = f"select * from {table}"',
    "schema variable in f-string": 's = "@"\nq = f"select * from {s}.snapshot"',
    "f-string literal part": 'q = f"select * from @.{table}"',
    "Identifier": 'sql.Identifier("@", "mqtt_message")',
    "bare Identifier": 'from psycopg.sql import Identifier\nIdentifier("@", "snapshot")',
    "Identifier on a variable": 's = "@"\nsql.Identifier(s, "snapshot")',
    "schema keyword": 'load.row_counts(conn, "x", schema="@")',
    "RAW_SCHEMA attribute": 'load.row_counts(conn, "x", schema=load.RAW_SCHEMA)',
    "RAW_SCHEMA name": 'load.row_counts(conn, "x", schema=RAW_SCHEMA)',
    "RAW_SCHEMA import": "from ingester.load import RAW_SCHEMA as default",
    "upper case": 'conn.execute("SELECT 1 FROM ^.SNAPSHOT")',
    "upper case constant": 'sql.Identifier("^", "snapshot")',
    "space around the dot": 'conn.execute("select 1 from @ . snapshot")',
    "trailing qualified prefix": 'conn.execute("delete from @." + table)',
    "trailing quoted prefix": "conn.execute('select 1 from \"@\".' + table)",
    "search_path set": 'conn.execute("set search_path to @")',
    "search_path list": 'conn.execute("set search_path = public, @")',
    "search_path option": 'psycopg.connect(options="-c search_path=@")',
    "f-string name part": 'name = f"@{SUFFIX}"',
    "f-string ending in the name": 'q = f"select * from @{dot}snapshot"',
    "string ending in the name": 'q = "select * from @" + "." + table',
    "bytes literal": 'conn.execute(b"select 1 from @.snapshot")',
    "REAL_SCHEMAS name": "touched = locked_relations(conn, REAL_SCHEMAS)",
    "REAL_SCHEMAS attribute": "names = raw_schema.REAL_SCHEMAS",
    "REAL_SCHEMAS import": "from raw_schema import REAL_SCHEMAS as watched",
}
# What the scan does not see; each probe shows it passes unseen. The database
# role queued for platform-eng is the guard for these.
NOT_SEEN = {
    "name built from pieces": 's = "r" + "aw"\nconn.execute(f"select 1 from {s}.snapshot")',
    "name joined at run time": 's = "".join(["r", "aw"])\nsql.Identifier(s, "snapshot")',
    "name from the environment": 's = os.environ["TEST_SCHEMA"]\nsql.Identifier(s, "snapshot")',
    "name from settings": 'sql.Identifier(settings.test_schema, "snapshot")',
    "name from a file": 's = Path("schema.txt").read_text()\nsql.Identifier(s, "snapshot")',
    "getattr on a built name": 's = getattr(load, "RAW_" + "SCHEMA")',
    "loader call without schema=": 'load.row_counts(conn, "x")',
    "register call without schema=": "register_verified(conn, folder)",
    "SQL read from another file": 'conn.execute(Path("fixture.sql").read_text())',
}


def probe(source: str, schema: str) -> str:
    """The probe for one watched name: @ as written, ^ in upper case."""
    return source.replace("@", schema).replace("^", schema.upper())


@pytest.mark.parametrize("schema", WATCHED)
@pytest.mark.parametrize("form", sorted(PROBES))
def test_the_scan_finds_each_form(form: str, schema: str) -> None:
    assert real_schema_uses(probe(PROBES[form], schema)), form


@pytest.mark.parametrize("form", sorted(NOT_SEEN))
def test_the_scan_does_not_see_these_forms(form: str) -> None:
    assert real_schema_uses(NOT_SEEN[form]) == [], form


def test_the_scan_leaves_docstrings_and_other_words_alone() -> None:
    docstring = '"""Loads into @.snapshot."""\n'.replace("@", WATCHED[0])
    others = 'draw.ground = 1\nname = "rawness"\nkey = "raw_bucket"\nrawdata.x = 1\n'
    assert real_schema_uses(docstring + others) == []


def test_the_scan_names_the_enclosing_function() -> None:
    source = 'def test_one():\n    s = "@"\n'.replace("@", WATCHED[0])
    assert real_schema_uses(source) == [("test_one", "constant 'raw'", 2)]


def test_no_test_module_names_raw_or_raw_dev_wh_outside_the_allowed_places() -> None:
    found: dict[tuple[str, str], list[str]] = defaultdict(list)
    lines: dict[tuple[str, str], list[int]] = defaultdict(list)
    for path in sorted(TESTS.rglob("*.py")):
        name = path.relative_to(TESTS).as_posix()
        for scope, kind, line in real_schema_uses(path.read_text(encoding="utf-8")):
            found[(name, scope)].append(kind)
            lines[(name, scope)].append(line)
    unexpected = {
        key: list(zip(kinds, lines[key], strict=True))
        for key, kinds in found.items()
        if sorted(kinds) != sorted(ALLOWED.get(key, []))
    }
    assert unexpected == {}
    assert set(ALLOWED) <= set(found), "an allowed place no longer matches; remove it"


def test_the_throwaway_schema_is_gone_after_use() -> None:
    with throwaway_raw_schema() as raw:
        assert schema_exists(raw.conn, raw.schema)
        name = raw.schema
    with connect_or_skip() as other:
        assert not schema_exists(other, name)


def test_the_throwaway_schema_is_gone_after_a_failure() -> None:
    names = []
    with pytest.raises(RuntimeError, match="a failing test"):
        with throwaway_raw_schema() as raw:
            names.append(raw.schema)
            raise RuntimeError("a failing test")
    with connect_or_skip() as other:
        assert not schema_exists(other, names[0])


def probe_name() -> str:
    """A new throwaway schema name; the caller creates, commits and drops it."""
    return f"{SCHEMA_PREFIX}{secrets.token_hex(6)}"


def create_probe(conn: psycopg.Connection, name: str) -> sql.Identifier:
    """Create and commit a schema holding one table, probe, and return the table."""
    probe = sql.Identifier(name, "probe")
    conn.execute(sql.SQL("create schema {}").format(sql.Identifier(name)))
    conn.execute(sql.SQL("create table {} (id integer)").format(probe))
    conn.commit()
    return probe


def test_the_lock_check_sees_a_table_the_transaction_only_read() -> None:
    # A new transaction holds no lock on a committed table until a select reads it.
    name = probe_name()
    with connect_or_skip() as conn:
        try:
            probe = create_probe(conn, name)
            assert locked_relations(conn, (name,)) == []
            conn.execute(sql.SQL("select id from {}").format(probe))
            assert locked_relations(conn, (name,)) == [f"{name}.probe"]
        finally:
            drop_throwaway(conn, name)


def test_the_teardown_fails_a_test_that_read_a_watched_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading a table in a watched schema fails the fixture at exit.

    A committed probe schema stands in for raw, with REAL_SCHEMAS pointed at it.
    The read is outside any savepoint: a savepoint that is rolled back releases
    the locks taken inside it, so the check cannot see a read made there.
    Without the lock check in throwaway_raw_schema this test fails.
    """
    name = probe_name()
    with connect_or_skip() as setup:
        try:
            probe = create_probe(setup, name)
            monkeypatch.setattr(raw_schema, "REAL_SCHEMAS", (name,))
            with pytest.raises(AssertionError, match="touched real tables"):
                with throwaway_raw_schema() as raw:
                    raw.conn.execute(sql.SQL("select id from {}").format(probe))
        finally:
            drop_throwaway(setup, name)


@pytest.mark.parametrize("name", [*WATCHED, "public", "test_load_", "test_load_x; drop"])
def test_only_a_throwaway_name_is_ever_dropped(name: str) -> None:
    # The refusal comes before any SQL, so no connection is needed.
    with pytest.raises(ValueError, match="never dropped"):
        drop_throwaway(None, name)  # type: ignore[arg-type]


def test_a_committed_throwaway_schema_is_dropped() -> None:
    name = probe_name()
    with connect_or_skip() as conn:
        create_probe(conn, name)
        try:
            assert schema_exists(conn, name)
        finally:
            drop_throwaway(conn, name)
        assert not schema_exists(conn, name)
