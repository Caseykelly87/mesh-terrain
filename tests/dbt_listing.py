"""The dbt project's nodes as `dbt ls --output json` lists them for one target.

`dbt ls` parses the project and renders every schema name through the
project's macros and source properties without opening a database
connection, so the tests read the schemas each target would build into, and
the tests each target would run, with no warehouse running. The profile's
`WAREHOUSE_*` variables must be set, as for `make check`; their values are
not used. Each target is listed once per test session.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from functools import cache
from pathlib import Path
from typing import Any

from dagster_dbt import DbtProject

REPO_ROOT = Path(__file__).resolve().parents[1]
DBT_DIR = REPO_ROOT / "dbt"
# make check and make dbt-build use the development target; the lead runs
# the real one (dbt/profiles.yml, dbt/README.md).
DEVELOPMENT_TARGET = "dev_wh"
REAL_TARGET = "prod"
OUTPUT_KEYS = ("unique_id", "name", "resource_type", "schema", "original_file_path", "config")


def dbt_executable() -> str:
    """The dbt command of the environment running the tests."""
    found = shutil.which("dbt", path=str(Path(sys.executable).parent)) or shutil.which("dbt")
    if found is None:
        raise RuntimeError("dbt is not installed in this environment; run uv sync")
    return found


def dbt_result(
    arguments: list[str], work_dir: Path, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run one dbt command against the project, writing its logs under work_dir."""
    command = [
        dbt_executable(),
        "--quiet",
        "--no-use-colors",
        *arguments,
        "--project-dir",
        str(DBT_DIR),
        "--profiles-dir",
        str(DBT_DIR),
        "--log-path",
        str(work_dir),
    ]
    return subprocess.run(command, capture_output=True, text=True, check=False, env=environment)


def run_dbt(arguments: list[str], work_dir: Path) -> str:
    """Run one dbt command and return its output, raising when it fails."""
    result = dbt_result(arguments, work_dir)
    if result.returncode != 0:
        raise RuntimeError(f"dbt {arguments[0]} failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout


@cache
def dbt_nodes(target: str) -> tuple[dict[str, Any], ...]:
    """Every node and source of the project, as dbt lists it for the target."""
    with tempfile.TemporaryDirectory() as work:
        work_dir = Path(work)
        # The definitions test installs packages the same way when they are
        # missing, as in CI's tests job; parsing needs dbt_utils' macros.
        if DbtProject(project_dir=DBT_DIR).has_uninstalled_deps:
            run_dbt(["deps"], work_dir)
        listing = run_dbt(
            [
                "ls",
                "--target",
                target,
                "--target-path",
                str(work_dir / "target"),
                "--output",
                "json",
                "--output-keys",
                *OUTPUT_KEYS,
            ],
            work_dir,
        )
    return tuple(json.loads(line) for line in listing.splitlines() if line.startswith("{"))
