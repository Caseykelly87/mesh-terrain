"""The dbt var raw_schema points the raw sources at a fixture schema on a development target only.

Casey's B3 loading path, item 2: the fixtures load into a throwaway
`test_fixture_<12 hex>` schema and the dbt sources read it through a variable.
The var is new on the dbt side (`dbt/macros/raw_schema.sql`). These tests
read `dbt ls`, which opens no database connection.
"""

import tempfile
from pathlib import Path

import pytest
from dbt_listing import DEVELOPMENT_TARGET, REAL_TARGET, dbt_nodes, dbt_result, raw_schema_vars

DEVELOPMENT_TARGETS = (DEVELOPMENT_TARGET, "dev_ta")
FIXTURE_SCHEMA = "test_fixture_0123456789ab"
READS_RAW = {"stg_packet", "stg_position", "fct_node_position"}
FIXTURE_ONLY = {
    "expected_fixture_positions",
    "assert_pos_01_fixture_positions",
    "assert_pos_02_fixture_positions_in_core",
    "assert_ing_06_first_copy_kept",
}


def names(nodes: tuple[dict[str, object], ...]) -> set[str]:
    return {str(node["name"]) for node in nodes}


def source_schemas(nodes: tuple[dict[str, object], ...]) -> set[str]:
    return {str(node["schema"]) for node in nodes if node["resource_type"] == "source"}


@pytest.mark.parametrize("target", DEVELOPMENT_TARGETS)
def test_without_the_var_a_development_target_builds_nothing_that_reads_raw(target: str) -> None:
    nodes = dbt_nodes(target)
    assert source_schemas(nodes) == {f"raw_{target}"}
    assert names(nodes) & (READS_RAW | FIXTURE_ONLY) == set()


@pytest.mark.parametrize("target", DEVELOPMENT_TARGETS)
def test_with_the_var_the_sources_read_the_fixture_schema(target: str) -> None:
    nodes = dbt_nodes(target, FIXTURE_SCHEMA)
    assert source_schemas(nodes) == {FIXTURE_SCHEMA}
    assert names(nodes) >= READS_RAW | FIXTURE_ONLY
    built = {node["schema"] for node in nodes if node["name"] in READS_RAW}
    assert built == {f"staging_{target}", f"core_{target}"}


def test_the_real_target_ignores_the_var() -> None:
    with_var = dbt_nodes(REAL_TARGET, FIXTURE_SCHEMA)
    assert source_schemas(with_var) == source_schemas(dbt_nodes(REAL_TARGET))
    assert FIXTURE_SCHEMA not in source_schemas(with_var)
    assert {node["unique_id"] for node in with_var} == {
        node["unique_id"] for node in dbt_nodes(REAL_TARGET)
    }
    assert names(with_var) >= READS_RAW
    assert names(with_var) & FIXTURE_ONLY == set()


def refusal(value: str) -> str:
    """What dbt prints when ls runs on the development target with raw_schema set to value."""
    with tempfile.TemporaryDirectory() as work:
        work_dir = Path(work)
        arguments = ["ls", "--target", DEVELOPMENT_TARGET, "--target-path", str(work_dir / "t")]
        result = dbt_result([*arguments, *raw_schema_vars(value)], work_dir)
    assert result.returncode != 0
    return result.stdout + result.stderr


@pytest.mark.parametrize(
    "value",
    [f"raw_{DEVELOPMENT_TARGET}", "test_fixture_xyz", "test_load_0123456789ab", "public"],
)
def test_a_var_that_is_not_a_fixture_schema_stops_dbt(value: str) -> None:
    assert "raw_schema must name a throwaway fixture schema" in refusal(value)


def test_the_real_raw_schema_is_refused_as_the_var() -> None:
    # The name comes from dbt's own listing of the real target's sources.
    (real_raw,) = source_schemas(dbt_nodes(REAL_TARGET))
    assert "raw_schema must name a throwaway fixture schema" in refusal(real_raw)
