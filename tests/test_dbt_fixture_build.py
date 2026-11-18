"""dbt builds the staging and core models on the synthetic fixtures and every test passes.

Casey's B3 loading path, item 2 (verbatim): "prefer loading the fixture into
its own throwaway schema (test_fixture_<random>), pointing dbt sources at it
through the existing raw-schema variable, and dropping it at the end, the
same pattern as test_load. No exception to the database role."

So each run creates a `test_fixture_<12 hex>` schema, builds the raw tables
in it from `ingester/sql/raw_tables.sql`, loads the link-rules and staging
fixtures (`tests/fixture_rows.py`), commits so dbt's own connections see them,
runs dbt on the development target with the var raw_schema naming the schema,
and drops it at the end, also after a failure. dbt's singular tests do the
comparisons with the hand-worked values: `assert_pos_01_fixture_positions`,
`assert_pos_02_fixture_positions_in_core`, `assert_ing_06_first_copy_kept`,
the link-rules cases' `assert_lnk_04_fixture_receipts` and
`assert_lnk_04_fixture_link_hours`, and the rest.

The build runs three times, after `dbt seed --full-refresh`. First `dbt run
--full-refresh` without the second fixture snapshot; then that snapshot is
loaded and `dbt build` runs incrementally, so the core model's incremental
path must pick up the new snapshot, replace a copy with an earlier one and add
a broker flag a later copy carries; then `dbt build --full-refresh`. Both
builds must pass every test, every singular test in `dbt/tests` among them,
and leave the same rows in `fct_node_position`, `fct_rx_observation` and
`int_link_window`.

Dropping the schema also drops the staging views on the development target
that read it (`cascade`); the next fixture build recreates them. Skipped where
no warehouse answers (CI's tests job).
"""

import json
import re
import secrets
import tempfile
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
import pytest
from dagster_dbt import DbtProject
from dbt_listing import DBT_DIR, DEVELOPMENT_TARGET, dbt_result, raw_schema_vars, run_dbt
from fixture_rows import all_rows, load_rows
from psycopg import sql
from raw_schema import connect_or_skip, schema_exists

from ingester import load

FIXTURE_PREFIX = "test_fixture_"
FIXTURE_PATTERN = re.compile(rf"{FIXTURE_PREFIX}[0-9a-f]{{12}}")
LATER_SNAPSHOT = "staging-fixture-b"
# The core tables whose incremental and full-refresh rows must agree, each
# read in full in its key order; geometry is compared as text.
CORE_TABLES = {
    "fct_node_position": "delivery_key",
    "fct_rx_observation": "delivery_key",
    "int_link_window": "tx_node_hash, gateway_node_hash, hour",
}
# Every singular test in dbt/tests runs in the fixture build, so a config
# change that disabled one fails the build test instead of passing quietly.
SINGULAR_TESTS = tuple(sorted(path.stem for path in (DBT_DIR / "tests").glob("*.sql")))
# The tests that report a count on every run warn by design: Casey's lnk_06
# count (2026-11-14) and the Q4 count lnk_01_direct_hop_contradicted (Casey,
# PR #72, Q1). Each must warn with the fixture's count.
REPORTING_TESTS = {
    "assert_lnk_06_excluded_count": 7,
    "assert_lnk_01_direct_hop_contradicted_count": 3,
}


def drop_fixture_schema(conn: psycopg.Connection, schema: str) -> None:
    """Drop one fixture schema and commit; any other name is refused before any SQL is sent."""
    if not FIXTURE_PATTERN.fullmatch(schema):
        raise ValueError(f"{schema!r} is not a fixture schema; it is never dropped")
    conn.rollback()
    conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema)))
    conn.commit()


@contextmanager
def fixture_schema() -> Iterator[tuple[psycopg.Connection, str]]:
    """A committed schema holding empty raw tables, dropped at exit."""
    schema = f"{FIXTURE_PREFIX}{secrets.token_hex(6)}"
    with connect_or_skip() as conn:
        try:
            conn.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
            load.apply_ddl(conn, schema=schema)
            conn.commit()
            yield conn, schema
        finally:
            if conn.closed or conn.broken:
                warnings.warn(
                    f"the connection was lost, so fixture schema {schema} was not dropped "
                    "and is left in the warehouse",
                    stacklevel=2,
                )
            else:
                drop_fixture_schema(conn, schema)


def dbt_on_fixture(arguments: list[str], schema: str, work_dir: Path) -> dict[str, str]:
    """Run one dbt command on the fixture schema; return each node's status by name."""
    target_path = work_dir / "target"
    command = [
        *arguments,
        "--target",
        DEVELOPMENT_TARGET,
        "--target-path",
        str(target_path),
        *raw_schema_vars(schema),
    ]
    result = dbt_result(command, work_dir)
    assert result.returncode == 0, f"dbt {arguments}:\n{result.stdout}\n{result.stderr}"
    results = json.loads((target_path / "run_results.json").read_text(encoding="utf-8"))
    return {
        entry["unique_id"].split(".")[2]: reported_status(entry) for entry in results["results"]
    }


def reported_status(entry: dict[str, Any]) -> str:
    """A node's status, a reporting test's warn with its expected count read as pass."""
    name = entry["unique_id"].split(".")[2]
    if name in REPORTING_TESTS and entry["status"] == "warn":
        return "pass" if entry["failures"] == REPORTING_TESTS[name] else f"warn {entry['failures']}"
    return str(entry["status"])


def core_rows(conn: psycopg.Connection) -> dict[str, list[tuple[Any, ...]]]:
    """Every row of each table in CORE_TABLES, in its key order."""
    rows = {}
    for table, key in CORE_TABLES.items():
        query = sql.SQL("select * from {} order by {}").format(
            sql.Identifier(f"core_{DEVELOPMENT_TARGET}", table), sql.SQL(key)
        )
        rows[table] = conn.execute(query).fetchall()
    conn.rollback()
    return rows


@pytest.fixture(scope="module")
def builds() -> Iterator[dict[str, Any]]:
    """The incremental and full-refresh builds' statuses and the core rows after each."""
    rows = all_rows()
    with tempfile.TemporaryDirectory() as work, fixture_schema() as (conn, schema):
        work_dir = Path(work)
        if DbtProject(project_dir=DBT_DIR).has_uninstalled_deps:
            run_dbt(["deps"], work_dir)
        load_rows(conn, schema, [row for row in rows if row["snapshot_id"] != LATER_SNAPSHOT])
        conn.commit()
        # The seed is rebuilt from its CSV first, so a column added to it since
        # the last run cannot meet the old table.
        dbt_on_fixture(["seed", "--full-refresh"], schema, work_dir)
        dbt_on_fixture(["run", "--full-refresh"], schema, work_dir)
        load_rows(conn, schema, [row for row in rows if row["snapshot_id"] == LATER_SNAPSHOT])
        conn.commit()
        incremental = dbt_on_fixture(["build"], schema, work_dir)
        incremental_rows = core_rows(conn)
        full_refresh = dbt_on_fixture(["build", "--full-refresh"], schema, work_dir)
        yield {
            "incremental": incremental,
            "full_refresh": full_refresh,
            "incremental_rows": incremental_rows,
            "full_refresh_rows": core_rows(conn),
        }


@pytest.mark.parametrize("build", ["incremental", "full_refresh"])
def test_every_node_of_the_fixture_build_passes(builds: dict[str, Any], build: str) -> None:
    failed = {
        name: status for name, status in builds[build].items() if status not in {"success", "pass"}
    }
    assert failed == {}


@pytest.mark.parametrize("build", ["incremental", "full_refresh"])
def test_the_fixture_build_runs_the_contract_tests(builds: dict[str, Any], build: str) -> None:
    assert set(SINGULAR_TESTS) <= set(builds[build])


def test_incremental_and_full_refresh_builds_leave_the_same_core_rows(
    builds: dict[str, Any],
) -> None:
    assert builds["incremental_rows"] == builds["full_refresh_rows"]
    counts = {table: len(rows) for table, rows in builds["full_refresh_rows"].items()}
    assert counts["fct_node_position"] > 100
    assert counts["fct_rx_observation"] > 100
    assert counts["int_link_window"] >= 40


def test_the_fixture_schema_is_gone_after_use() -> None:
    with fixture_schema() as (_, schema):
        pass
    with connect_or_skip() as conn:
        assert not schema_exists(conn, schema)


def test_the_fixture_schema_is_gone_after_a_failure() -> None:
    names = []
    with pytest.raises(RuntimeError), fixture_schema() as (_, schema):
        names.append(schema)
        raise RuntimeError("a failing test")
    with connect_or_skip() as conn:
        assert not schema_exists(conn, names[0])


@pytest.mark.parametrize("name", ["public", "test_load_0123456789ab", "test_fixture_", "x; drop"])
def test_only_a_fixture_schema_is_ever_dropped(name: str) -> None:
    with pytest.raises(ValueError, match="never dropped"):
        drop_fixture_schema(None, name)  # type: ignore[arg-type]
