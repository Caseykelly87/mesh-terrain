"""Which schemas each dbt target reads and writes (issue #55, B1).

There are two development targets, one per track, so two `make check` runs
at once never build into the same schemas (Casey, issue #55 comment
6966197127, item 3). `dev_wh` is the profile's default and Track B's: every
relation it builds sits in a schema suffixed `_dev_wh`, and every source reads
`raw_dev_wh`. `dev_ta` is Track A's, through `make check DBT_DEV_TARGET=dev_ta`:
suffix `_dev_ta`, sources in `raw_dev_ta`. Both raw schemas hold synthetic
fixture tables only. The real `raw`, `staging`, `core` and `analytics` schemas
belong to the target `prod`. dbt also takes the target from the `DBT_TARGET`
environment variable, so the target alone does not keep a build off the real
schemas: a run on `prod` refuses to start unless
`--vars '{real_build: true}'` is passed, which only the lead's
`make dbt-build-real` does, and `make check` and `make dbt-build` refuse
`DBT_DEV_TARGET=prod`.
"""

import os
import re
import shutil
import subprocess
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

# Track A's development target; DEVELOPMENT_TARGET (dev_wh) is Track B's.
TRACK_A_TARGET = "dev_ta"
DEVELOPMENT_TARGETS = (DEVELOPMENT_TARGET, TRACK_A_TARGET)
BUILT_SCHEMAS = ("staging", "core", "analytics")
CREATED_SCHEMA = re.compile(r'create schema if not exists "([^"]+)"', re.IGNORECASE)
REAL_SCHEMAS = {"raw", "staging", "core", "analytics"}
REAL_BUILD_FLAG = "{real_build: true}"
SQL_FOLDERS = ("models", "tests", "snapshots", "macros", "analyses")
JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.DOTALL)
SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
SCHEMA_REFERENCE = re.compile(
    r'(?<![\w."])"?(raw|staging|core|analytics|features)"?\s*\.\s*"?[a-z_]', re.IGNORECASE
)
MAKE_MODIFIERS = r"(?:(?:export|override|private)[ \t]+)*"
MAKE_VARIABLE = r"([A-Za-z_][\w.-]*)"
# An optional target list before the variable makes it target-specific; the
# lookahead keeps `NAME := value` from reading as target `NAME`.
# Leading spaces, as inside `ifeq ... endif`, are allowed; a leading tab makes
# a recipe line, which is shell, not make.
MAKE_ASSIGNMENT = (
    rf"^ *(?:[^\s:#=][^:#=\n]*:(?!:*=)[ \t]*)?{MAKE_MODIFIERS}{MAKE_VARIABLE}"
    r"[ \t]*(?::{1,3}|[+?!])?=(.*)$"
)
MAKE_DEFINE = rf"^ *{MAKE_MODIFIERS}define[ \t]+{MAKE_VARIABLE}[^\n]*\n(.*?)^ *endef\b"


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


def hook_schemas(target: str) -> set[str]:
    """The schemas the on-run-start macro would create on the target.

    `dbt compile --inline` renders the macro and prints its SQL without running
    it; dbt only lists the target's relations, so this writes nothing.
    """
    with tempfile.TemporaryDirectory() as work:
        result = dbt_result(
            [
                "compile",
                "--inline",
                "{{ create_target_schemas() }}",
                "--target",
                target,
                "--target-path",
                str(Path(work) / "target"),
            ],
            Path(work),
        )
    assert result.returncode == 0, result.stdout + result.stderr
    return set(CREATED_SCHEMA.findall(result.stdout))


def make_dry_run(
    *arguments: str, dbt_dev_target: str | None = None
) -> subprocess.CompletedProcess[str]:
    """`make -n` in the checkout, printing the recipes it would run and running none.

    The environment loses what a calling make passes down (MAKEFLAGS carries
    its command-line variables) and any DBT_DEV_TARGET, so `make check
    DBT_DEV_TARGET=dev_ta` running this test does not change what it sees;
    dbt_dev_target, when given, puts one back in the environment.
    """
    make = shutil.which("make")
    if make is None:
        pytest.skip("make is not installed")
    passed_down = {"MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "DBT_DEV_TARGET"}
    environment = {name: value for name, value in os.environ.items() if name not in passed_down}
    if dbt_dev_target is not None:
        environment["DBT_DEV_TARGET"] = dbt_dev_target
    return subprocess.run(
        [make, "--no-print-directory", "-n", *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


def variable_assignments(makefile: str) -> list[tuple[str, str]]:
    """Every (name, value) a Makefile assigns, in the forms GNU make accepts.

    It reads `=`, `:=`, `::=`, `:::=`, `?=`, `+=` and `!=`, with any
    `export`, `override` or `private` prefix, after a target as a
    target-specific variable, across backslash-continued lines, and
    `define` blocks up to `endef`, each indented with spaces or not. It does
    not see a variable set inside `$(eval ...)`, in an included file, under a
    computed name such as `$(PREFIX)_FLAG`, or on a tab-indented line inside
    `ifeq ... endif` after a variable definition, which make reads as an
    assignment and the scan skips as a recipe line; and `references` does not
    see one read through `$(value FLAG)`.
    """
    joined = makefile.replace("\\\n", " ")
    return [
        *re.findall(MAKE_ASSIGNMENT, joined, re.MULTILINE),
        *re.findall(MAKE_DEFINE, joined, re.MULTILINE | re.DOTALL),
    ]


def references(text: str, name: str) -> bool:
    """Whether text expands the variable: `$(NAME)`, `${NAME}`, `$(NAME:a=b)`, or `$N`."""
    pattern = rf"\$[({{]{re.escape(name)}[:)}}]"
    if len(name) == 1:
        pattern += rf"|\${re.escape(name)}"
    return re.search(pattern, text) is not None


def recipes_using(makefile: str, word: str) -> list[str]:
    """The Makefile targets whose recipe holds the word, directly or through a variable.

    A variable holds the word when its value does, or expands a variable that
    holds it, so `FLAG := --vars '{real_build: true}'` and a recipe using
    `$(FLAG)` count as passing it. A target-specific variable counts for every
    recipe that expands it, which can only add targets. The limits are those of
    `variable_assignments`.
    """
    assignments = variable_assignments(makefile)
    holding: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, value in assignments:
            if name not in holding and (
                word in value or any(references(value, held) for held in holding)
            ):
                holding.add(name)
                changed = True
    recipes = re.findall(r"^([\w-]+):[^\n]*\n((?:\t[^\n]*\n)+)", makefile, re.MULTILINE)
    return [
        name
        for name, recipe in recipes
        if word in recipe or any(references(recipe, held) for held in holding)
    ]


def hard_coded_schemas(sql: str) -> list[str]:
    """Warehouse schema names written into SQL outside Jinja and comments."""
    return SCHEMA_REFERENCE.findall(SQL_COMMENT.sub(" ", JINJA.sub(" ", sql)))


def test_the_default_target_is_the_development_target() -> None:
    profiles = yaml.safe_load((DBT_DIR / "profiles.yml").read_text(encoding="utf-8"))
    profile = profiles["mesh_terrain"]
    assert profile["target"] == DEVELOPMENT_TARGET
    assert set(profile["outputs"]) == {*DEVELOPMENT_TARGETS, REAL_TARGET}


def test_every_development_target_connects_as_the_real_target_does() -> None:
    """Only the target's name differs, and the name alone picks the schemas."""
    outputs = yaml.safe_load((DBT_DIR / "profiles.yml").read_text(encoding="utf-8"))[
        "mesh_terrain"
    ]["outputs"]
    assert all(outputs[target] == outputs[REAL_TARGET] for target in DEVELOPMENT_TARGETS)


@pytest.mark.parametrize("target", DEVELOPMENT_TARGETS)
def test_every_relation_on_the_development_target_is_in_a_suffixed_schema(target: str) -> None:
    nodes = in_target_schemas(dbt_nodes(target))
    assert {node["resource_type"] for node in nodes} >= {"model", "seed", "test"}
    outside = [
        (node["unique_id"], node["schema"])
        for node in nodes
        if not node["schema"].endswith(f"_{target}")
    ]
    assert outside == []


@pytest.mark.parametrize("target", DEVELOPMENT_TARGETS)
def test_every_source_on_the_development_target_reads_the_synthetic_raw_schema(
    target: str,
) -> None:
    suffix = f"_{target}"
    found = sources(dbt_nodes(target))
    assert found, "no dbt source is declared, so the source check would pass on nothing"
    assert {node["schema"] for node in found} == {f"raw{suffix}"}


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
@pytest.mark.parametrize("target", DEVELOPMENT_TARGETS)
def test_the_development_target_runs_without_the_flag(target: str) -> None:
    assert schema_hook(target) == ""


@pytest.mark.usefixtures("warehouse")
@pytest.mark.parametrize("target", DEVELOPMENT_TARGETS)
def test_the_hook_creates_every_schema_the_development_target_reads_or_builds(
    target: str,
) -> None:
    read = {node["schema"] for node in sources(dbt_nodes(target))}
    built = {f"{base}_{target}" for base in BUILT_SCHEMAS}
    assert len(read) == 1
    assert hook_schemas(target) == read | built


def test_make_dbt_build_uses_the_track_b_target_by_default() -> None:
    result = make_dry_run("dbt-build")
    assert result.returncode == 0, result.stderr
    assert f"--target {DEVELOPMENT_TARGET}" in result.stdout


def test_make_dbt_build_and_check_pass_the_development_target_they_are_given() -> None:
    for goal in ("dbt-build", "check"):
        result = make_dry_run(goal, f"DBT_DEV_TARGET={TRACK_A_TARGET}")
        assert result.returncode == 0, result.stderr
        assert f"--target {TRACK_A_TARGET}" in result.stdout
        assert f"--target {DEVELOPMENT_TARGET}" not in result.stdout


@pytest.mark.parametrize("goal", ["check", "dbt-build"])
@pytest.mark.parametrize("value", [REAL_TARGET, "", "dev_wh dev_ta", "dev"])
def test_make_refuses_any_value_but_a_development_target(goal: str, value: str) -> None:
    """Refused before make prints or runs any recipe, so `make check` lints nothing first."""
    result = make_dry_run(goal, f"DBT_DEV_TARGET={value}")
    assert result.returncode != 0
    assert "make dbt-build-real" in result.stderr
    assert result.stdout == ""


def test_make_refuses_the_real_target_from_the_environment() -> None:
    result = make_dry_run("check", dbt_dev_target=REAL_TARGET)
    assert result.returncode != 0
    assert "make dbt-build-real" in result.stderr
    assert result.stdout == ""


@pytest.mark.usefixtures("warehouse")
def test_a_real_build_value_other_than_true_is_refused() -> None:
    assert "make dbt-build-real" in schema_hook(REAL_TARGET, "--vars", "{real_build: 'true'}")
    assert "make dbt-build-real" in schema_hook(REAL_TARGET, "--vars", "{real_build: 1}")


def test_the_makefile_scan_follows_variables() -> None:
    makefile = (
        "FLAG := --vars '{real_build: true}'\n"
        "OTHER = $(FLAG)\n"
        "QUIET = --quiet\n"
        "via-variable:\n"
        "\t$(DBT) build ${OTHER}\n"
        "direct:\n"
        "\tdbt build --vars '{real_build: true}'\n"
        "plain:\n"
        "\tdbt build $(QUIET)\n"
    )
    assert recipes_using(makefile, "real_build") == ["via-variable", "direct"]


FLAG_VALUE = "--vars '{real_build: true}'"


@pytest.mark.parametrize(
    "assignment",
    [
        f"FLAG ::= {FLAG_VALUE}\n",
        f"FLAG :::= {FLAG_VALUE}\n",
        f"FLAG != echo {FLAG_VALUE}\n",
        f"export FLAG := {FLAG_VALUE}\n",
        f"override FLAG = {FLAG_VALUE}\n",
        f"override export FLAG += {FLAG_VALUE}\n",
        f"uses: FLAG = {FLAG_VALUE}\n",
        f"other another: export FLAG := {FLAG_VALUE}\n",
        "FLAG := --vars \\\n  '{real_build: true}'\n",
        f"define FLAG\n{FLAG_VALUE}\nendef\n",
        f"override define FLAG :=\n  --quiet\n  {FLAG_VALUE}\nendef\n",
        f"ifeq ($(CI),)\n  FLAG := {FLAG_VALUE}\nendif\n",
        f"ifdef CI\n  export FLAG = {FLAG_VALUE}\nendif\n",
        f"ifndef CI\n  define FLAG\n  {FLAG_VALUE}\n  endef\nendif\n",
    ],
)
def test_the_makefile_scan_reads_every_assignment_form(assignment: str) -> None:
    makefile = f"{assignment}uses:\n\t$(DBT) build $(FLAG)\nplain:\n\tdbt build --quiet\n"
    assert recipes_using(makefile, "real_build") == ["uses"]


def test_the_makefile_scan_reads_substitution_references() -> None:
    makefile = f"FLAG = {FLAG_VALUE}\nuses:\n\tdbt build $(FLAG:x=y)\n"
    assert recipes_using(makefile, "real_build") == ["uses"]


def test_the_makefile_scan_ignores_an_assignment_in_a_comment() -> None:
    makefile = f"# FLAG = {FLAG_VALUE}\nplain:\n\tdbt build $(FLAG)\n"
    assert recipes_using(makefile, "real_build") == []


def test_only_make_dbt_build_real_passes_the_real_build_flag() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert recipes_using(makefile, "real_build") == ["dbt-build-real"]


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
