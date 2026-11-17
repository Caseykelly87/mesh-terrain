"""Which schemas each dbt target reads and writes (issue #55, B1).

The development target `dev_wh` is the profile's default and the one
`make check` and `make dbt-build` name: every relation it builds sits in a
schema suffixed `_dev_wh`, and every source reads `raw_dev_wh`, which holds
synthetic fixture tables only. The real `raw`, `staging`, `core` and
`analytics` schemas belong to the target `prod`. dbt also takes the target
from the `DBT_TARGET` environment variable, so the target alone does not keep
a build off the real schemas: a run on `prod` refuses to start unless
`--vars '{real_build: true}'` is passed, which only the lead's
`make dbt-build-real` does.
"""

import os
import re
import tempfile
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml
from dbt_listing import (
    DBT_DIR,
    DEVELOPMENT_TARGET,
    REAL_TARGET,
    REPO_ROOT,
    dbt_nodes,
    dbt_result,
)

from pipeline.common.settings import get_settings

SUFFIX = f"_{DEVELOPMENT_TARGET}"
REAL_SCHEMAS = {"raw", "staging", "core", "analytics"}
REAL_BUILD_FLAG = "{real_build: true}"
SQL_FOLDERS = ("models", "tests", "snapshots", "macros", "analyses")
JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.DOTALL)
SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
SCHEMA_REFERENCE = re.compile(
    r'(?<![\w."])"?(raw|staging|core|analytics|features)"?\s*\.\s*"?[a-z_]', re.IGNORECASE
)


def in_target_schemas(nodes: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    """Every node the target places in a schema: all but sources, whatever its type."""
    return [node for node in nodes if node["resource_type"] != "source" and node.get("schema")]


def sources(nodes: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    return [node for node in nodes if node["resource_type"] == "source"]


@pytest.fixture
def warehouse() -> None:
    """Skip when no warehouse answers: dbt run-operation opens a connection first."""
    settings = get_settings()
    try:
        psycopg.connect(
            host=settings.warehouse_host,
            port=settings.warehouse_port,
            user=settings.warehouse_user,
            password=settings.warehouse_password.get_secret_value(),
            dbname=settings.warehouse_db,
            connect_timeout=3,
        ).close()
    except psycopg.OperationalError as err:
        pytest.skip(f"no local warehouse ({err.__class__.__name__}); run make up")


def schema_hook(target: str, *extra: str, environment: dict[str, str] | None = None) -> str:
    """Run the on-run-start macro alone and return dbt's output; '' means it ran.

    run-operation renders the macro, raising its refusal, and runs none of the
    SQL it returns, so this writes nothing on any target. It reads no schema.
    """
    target_arguments = ["--target", target] if target else []
    with tempfile.TemporaryDirectory() as work:
        result = dbt_result(
            ["run-operation", "create_target_schemas", *target_arguments, *extra],
            Path(work),
            environment,
        )
    return "" if result.returncode == 0 else result.stdout + result.stderr


def hard_coded_schemas(sql: str) -> list[str]:
    """Warehouse schema names written into SQL outside Jinja and comments."""
    return SCHEMA_REFERENCE.findall(SQL_COMMENT.sub(" ", JINJA.sub(" ", sql)))


def test_the_default_target_is_the_development_target() -> None:
    profiles = yaml.safe_load((DBT_DIR / "profiles.yml").read_text(encoding="utf-8"))
    profile = profiles["mesh_terrain"]
    assert profile["target"] == DEVELOPMENT_TARGET
    assert set(profile["outputs"]) == {DEVELOPMENT_TARGET, REAL_TARGET}


def test_every_relation_on_the_development_target_is_in_a_suffixed_schema() -> None:
    nodes = in_target_schemas(dbt_nodes(DEVELOPMENT_TARGET))
    assert {node["resource_type"] for node in nodes} >= {"model", "seed", "test"}
    outside = [
        (node["unique_id"], node["schema"]) for node in nodes if not node["schema"].endswith(SUFFIX)
    ]
    assert outside == []


def test_every_source_on_the_development_target_reads_the_synthetic_raw_schema() -> None:
    found = sources(dbt_nodes(DEVELOPMENT_TARGET))
    assert found, "no dbt source is declared, so the source check would pass on nothing"
    assert {node["schema"] for node in found} == {f"raw{SUFFIX}"}


def test_the_real_target_builds_the_real_schemas_and_reads_the_real_raw() -> None:
    nodes = dbt_nodes(REAL_TARGET)
    models = [node for node in nodes if node["resource_type"] in {"model", "seed"}]
    assert any(node["resource_type"] == "model" for node in models)
    assert {node["schema"] for node in models} <= REAL_SCHEMAS - {"raw"}
    assert {node["schema"] for node in sources(nodes)} == {"raw"}


@pytest.mark.usefixtures("warehouse")
def test_the_real_target_refuses_to_run_without_the_real_build_flag() -> None:
    assert "make dbt-build-real" in schema_hook(REAL_TARGET)


@pytest.mark.usefixtures("warehouse")
def test_dbt_target_in_the_environment_cannot_start_a_real_build() -> None:
    environment = {**os.environ, "DBT_TARGET": REAL_TARGET}
    assert "make dbt-build-real" in schema_hook("", environment=environment)


@pytest.mark.usefixtures("warehouse")
def test_the_real_target_runs_with_the_real_build_flag() -> None:
    assert schema_hook(REAL_TARGET, "--vars", REAL_BUILD_FLAG) == ""


@pytest.mark.usefixtures("warehouse")
def test_the_development_target_runs_without_the_flag() -> None:
    assert schema_hook(DEVELOPMENT_TARGET) == ""


def test_only_make_dbt_build_real_passes_the_real_build_flag() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    recipes = re.findall(r"^([\w-]+):[^\n]*\n((?:\t[^\n]*\n)+)", makefile, re.MULTILINE)
    passing = [name for name, recipe in recipes if "real_build" in recipe]
    assert passing == ["dbt-build-real"]


def test_the_scanner_finds_schema_names_written_into_sql() -> None:
    sql = (
        "-- staging.not_counted in a comment\n"
        "select * from raw.mqtt_message\n"
        'join "core"."fct_node_position" using (node)\n'
        "join {{ source('raw', 'snapshot') }} using (snapshot_id)\n"
        "join information_schema.schemata on true\n"
        "join {{ ref('stg_packet') }} as staging_rows on true\n"
    )
    assert hard_coded_schemas(sql) == ["raw", "core"]


def test_no_dbt_sql_names_a_warehouse_schema_outside_jinja() -> None:
    found = {
        path.relative_to(DBT_DIR).as_posix(): names
        for folder in SQL_FOLDERS
        for path in sorted((DBT_DIR / folder).rglob("*.sql"))
        if (names := hard_coded_schemas(path.read_text(encoding="utf-8")))
    }
    assert found == {}
