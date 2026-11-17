"""No test sends SQL to the real raw tables, and the throwaway schema is gone after each use.

Casey, issue #55 comment 6960283869, item 10: the loader tests use their own
throwaway schema, never `raw` and never `raw_dev_wh`. The source scan reads
every test module; the database tests need the local stack and skip without it.
"""

import ast
import re
import secrets
from pathlib import Path

import pytest
from psycopg import sql
from raw_schema import (
    REAL_SCHEMAS,
    SCHEMA_PREFIX,
    connect_or_skip,
    drop_throwaway,
    locked_relations,
    schema_exists,
    throwaway_raw_schema,
)

TESTS = Path(__file__).resolve().parent
QUALIFIED = re.compile(rf"\b({'|'.join(REAL_SCHEMAS)})\.")
SQL_WORDS = re.compile(
    r"\b(select|insert|update|delete|alter|create|drop|truncate|copy|from|into|table)\b",
    re.IGNORECASE,
)


def docstrings(tree: ast.Module) -> set[int]:
    """The ids of the string nodes that are docstrings, which describe rather than run SQL."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def real_schema_sql(source: str) -> list[str]:
    """String literals that run SQL against raw or raw_dev_wh, and identifiers built on them."""
    tree = ast.parse(source)
    skip = docstrings(tree)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            if QUALIFIED.search(node.value) and SQL_WORDS.search(node.value):
                hits.append(f"line {node.lineno}: {node.value[:60]!r}")
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "Identifier":
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and first.value in REAL_SCHEMAS:
                hits.append(f"line {node.lineno}: Identifier({first.value!r}, ...)")
        if isinstance(node, ast.keyword) and node.arg == "schema":
            if isinstance(node.value, ast.Constant) and node.value.value in REAL_SCHEMAS:
                hits.append(f"line {node.value.lineno}: schema={node.value.value!r}")
    return hits


@pytest.mark.parametrize(
    "line",
    [
        # Built from the names, so this file holds no such literal itself.
        f'conn.execute("update {REAL_SCHEMAS[0]}.mqtt_message set rx_snr = 0")',
        f'conn.execute("select count(*) from {REAL_SCHEMAS[1]}.snapshot")',
        'sql.Identifier("raw", "mqtt_message")',
        'load.row_counts(conn, "x", schema="raw_dev_wh")',
    ],
)
def test_the_scan_finds_sql_against_a_real_schema(line: str) -> None:
    assert real_schema_sql(line)


def test_the_scan_leaves_docstrings_and_other_words_alone() -> None:
    docstring = f'"""Loads into {REAL_SCHEMAS[0]}.snapshot."""\n'
    source = docstring + 'draw.ground = 1\nkey = ["raw", "mqtt_snapshot"]\n'
    assert real_schema_sql(source) == []


def test_no_test_module_sends_sql_to_raw_or_raw_dev_wh() -> None:
    found = {
        path.relative_to(TESTS).as_posix(): hits
        for path in sorted(TESTS.rglob("*.py"))
        if (hits := real_schema_sql(path.read_text(encoding="utf-8")))
    }
    assert found == {}


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


def test_the_lock_check_sees_a_table_the_transaction_only_read() -> None:
    # The teardown's guard against reaching raw, shown on a committed throwaway
    # schema: a new transaction holds no lock on it until a select reads its table.
    name = f"{SCHEMA_PREFIX}{secrets.token_hex(6)}"
    probe = sql.Identifier(name, "probe")
    with connect_or_skip() as conn:
        try:
            conn.execute(sql.SQL("create schema {}").format(sql.Identifier(name)))
            conn.execute(sql.SQL("create table {} (id integer)").format(probe))
            conn.commit()
            assert locked_relations(conn, (name,)) == []
            conn.execute(sql.SQL("select id from {}").format(probe))
            assert locked_relations(conn, (name,)) == [f"{name}.probe"]
            conn.rollback()
        finally:
            drop_throwaway(conn, name)


@pytest.mark.parametrize("name", [*REAL_SCHEMAS, "public", "test_load_", "test_load_x; drop"])
def test_only_a_throwaway_name_is_ever_dropped(name: str) -> None:
    # The refusal comes before any SQL, so no connection is needed.
    with pytest.raises(ValueError, match="never dropped"):
        drop_throwaway(None, name)  # type: ignore[arg-type]


def test_a_committed_throwaway_schema_is_dropped() -> None:
    name = f"{SCHEMA_PREFIX}{secrets.token_hex(6)}"
    with connect_or_skip() as conn:
        conn.execute(sql.SQL("create schema if not exists {}").format(sql.Identifier(name)))
        conn.commit()
        try:
            assert schema_exists(conn, name)
        finally:
            drop_throwaway(conn, name)
        assert not schema_exists(conn, name)
