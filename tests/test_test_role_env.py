"""How the test role's name and password reach make: from the shell, never from a copied .env.

Casey, PR #74: "They must never receive a copy of .env or NODE_ID_HMAC_KEY.
.env.example gets the two names with placeholder values only." So
`.env.example` lists the two names commented out, a worktree's `.env` copied
from it sets neither, and a check in a worktree gets them from the shell,
which exports only those two lines of the main checkout's `.env` (README,
Test role). A value from the shell wins over any `.env` that still sets one,
and with neither, the goals that connect as the role stop before any recipe
runs.

Every make run here is a dry run (`make -n`) of the checkout's Makefile from
a temporary folder, so it reads only the `.env` a test writes there and runs
no recipe; the values are invented.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
NAMES = ("WAREHOUSE_TEST_USER", "WAREHOUSE_TEST_PASSWORD")
# What a calling make passes down, which would change what these runs see.
PASSED_DOWN = {"MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "DBT_DEV_TARGET"}
# The main checkout's .env as the README recipe names it, in Git Bash form.
MAIN_DOTENV = "/c/Users/Casey/Desktop/GEO/mesh-terrain/.env"
SHOW = "show: ; @echo user=$(WAREHOUSE_TEST_USER) password=$(WAREHOUSE_TEST_PASSWORD)"


def dry_run(
    *arguments: str, directory: Path, shell: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """`make -n` in directory with the two names taken from shell alone."""
    make = shutil.which("make")
    if make is None:
        pytest.skip("make is not installed")
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in PASSED_DOWN and name not in NAMES
    }
    environment.update(shell)
    return subprocess.run(
        [make, "--no-print-directory", "-n", "-f", str(REPO_ROOT / "Makefile"), *arguments],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


def test_the_env_example_sets_neither_name() -> None:
    lines = (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    assert [line for line in lines if line.startswith(NAMES)] == []
    for name in NAMES:
        assert any(re.fullmatch(rf"# {name}=<[a-z ]+>", line) for line in lines), name


@pytest.mark.parametrize("goal", ["check", "dbt-build", "dbt-debug", "test-role", "up"])
@pytest.mark.parametrize("missing", NAMES)
def test_a_goal_that_connects_as_the_role_stops_without_it(
    goal: str, missing: str, tmp_path: Path
) -> None:
    shell = {name: "invented" for name in NAMES if name != missing}
    result = dry_run(goal, directory=tmp_path, shell=shell)
    assert result.returncode != 0
    assert f"set {missing}" in result.stderr
    assert result.stdout == ""


def test_a_shell_value_wins_over_a_dotenv_that_sets_one(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "WAREHOUSE_TEST_USER=from_file\nWAREHOUSE_TEST_PASSWORD=file_secret\n", encoding="utf-8"
    )
    shell = {"WAREHOUSE_TEST_USER": "from_shell", "WAREHOUSE_TEST_PASSWORD": "shell_secret"}
    result = dry_run("--eval", SHOW, "show", directory=tmp_path, shell=shell)
    assert result.returncode == 0, result.stderr
    assert "user=from_shell password=shell_secret" in result.stdout


def test_the_main_checkouts_dotenv_still_supplies_them_alone(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "WAREHOUSE_TEST_USER=from_file\nWAREHOUSE_TEST_PASSWORD=file_secret\n", encoding="utf-8"
    )
    result = dry_run("--eval", SHOW, "show", directory=tmp_path, shell={})
    assert result.returncode == 0, result.stderr
    assert "user=from_file password=file_secret" in result.stdout


def test_the_shell_export_recipe_takes_only_the_two_lines(tmp_path: Path) -> None:
    """The README recipe, run on an invented .env, exports the two names and nothing else."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not installed")
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    recipe = re.search(r"^(while IFS= read -r line; do .+)$", readme, re.MULTILINE)
    assert recipe is not None, "the README has no export recipe"
    main = tmp_path / "main"
    main.mkdir()
    (main / ".env").write_text(
        "WAREHOUSE_USER=owner\r\nNODE_ID_HMAC_KEY=" + "ab" * 32 + "\r\n"
        "WAREHOUSE_TEST_USER=role_x\r\nWAREHOUSE_TEST_PASSWORD=pw_x\r\n",
        encoding="utf-8",
        newline="",
    )
    line = recipe.group(1).replace(MAIN_DOTENV, (main / ".env").as_posix())
    result = subprocess.run(
        [bash, "-c", line + "; env"],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": os.environ.get("PATH", "")},
    )
    assert result.returncode == 0, result.stderr
    exported = dict(entry.split("=", 1) for entry in result.stdout.splitlines() if "=" in entry)
    assert exported.get("WAREHOUSE_TEST_USER") == "role_x"
    assert exported.get("WAREHOUSE_TEST_PASSWORD") == "pw_x"
    assert "NODE_ID_HMAC_KEY" not in exported
    assert "WAREHOUSE_USER" not in exported
