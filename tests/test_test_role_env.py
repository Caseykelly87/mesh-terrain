"""How the test role's name and password reach make: from the shell, never printed.

Casey, PR #74: "They must never receive a copy of .env or NODE_ID_HMAC_KEY.
.env.example gets the two names with placeholder values only." And issue #55
comment 7017280290, item 2: "Export only the two values by name, with no
command that prints them (no echo, no env dump, no set -x), so they never
appear in a transcript or log."

So `.env.example` lists the two names commented out, a worktree's `.env`
copied from it sets neither, and a check in a worktree gets them from the
shell, which exports only those two lines of the main checkout's `.env`
(README, Test role). A value from the shell wins over any `.env` that still
sets one; without them, the goals that connect as the role stop before any
recipe runs. No recipe expands either value in make, so neither `make -n`
nor make's echo of a recipe line shows one, and `make dbt-debug`, whose dbt
command prints the connection's user, runs as the owner.

Every make run here uses the checkout's Makefile from a temporary folder, so
it reads only the `.env` a test writes there, with invented values; the
real ones are never read. The checks on values compare them with `test`
inside make, which prints nothing.
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
RECIPE_START = "while IFS= read -r line; do "
# Shell words that would print a value or the environment, or trace commands.
# A command word counts only on its own, so the `env` in `.env` does not.
PRINTING = re.compile(
    r"(?<![\w./-])(echo|printf|env|printenv|declare|typeset|cat|tee|less|more|set)(?![\w.-])"
    r"|export -p|\$-|(?<![\w-])-x\b"
)
INVENTED_USER = "invented_user_7c1f"
INVENTED_PASSWORD = "invented_password_9d2e"
# The goals that connect as the role and so need both values.
NEEDS_THE_ROLE = ("check", "dbt-build", "test-role", "up")


def make_run(
    *arguments: str, directory: Path, shell: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """make with the checkout's Makefile in directory, with the two names taken from shell alone."""
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
        [make, "--no-print-directory", "-f", str(REPO_ROOT / "Makefile"), *arguments],
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


def values_are(user: str, password: str) -> str:
    """A make target that exits 0 only when the two values are these, and prints nothing."""
    return (
        f'values-are: ; @test "$(WAREHOUSE_TEST_USER)" = "{user}" '
        f'&& test "$(WAREHOUSE_TEST_PASSWORD)" = "{password}"'
    )


def recipe_lines() -> dict[str, list[str]]:
    """Every copy of the export recipe, by the file that carries it."""
    found = {}
    for name in ("README.md", ".env.example", "docs/STATUS.md"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        found[name] = re.findall(rf"{re.escape(RECIPE_START)}[^`\n]+", text)
    return found


def test_the_env_example_sets_neither_name() -> None:
    lines = (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    assert [line for line in lines if line.startswith(NAMES)] == []
    for name in NAMES:
        assert any(re.fullmatch(rf"# {name}=<[a-z ]+>", line) for line in lines), name


@pytest.mark.parametrize("goal", NEEDS_THE_ROLE)
@pytest.mark.parametrize("missing", NAMES)
def test_a_goal_that_connects_as_the_role_stops_without_it(
    goal: str, missing: str, tmp_path: Path
) -> None:
    shell = {name: INVENTED_USER for name in NAMES if name != missing}
    result = make_run("-n", goal, directory=tmp_path, shell=shell)
    assert result.returncode != 0
    assert f"set {missing}" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("goal", [*NEEDS_THE_ROLE, "dbt-debug", "dbt-build-real", "help"])
def test_no_goal_shows_either_value_in_make_output(goal: str, tmp_path: Path) -> None:
    shell = {"WAREHOUSE_TEST_USER": INVENTED_USER, "WAREHOUSE_TEST_PASSWORD": INVENTED_PASSWORD}
    result = make_run("-n", goal, directory=tmp_path, shell=shell)
    output = result.stdout + result.stderr
    shown = [value for value in (INVENTED_USER, INVENTED_PASSWORD) if value in output]
    assert shown == [], f"make -n {goal} shows a test role value"


def test_dbt_debug_runs_as_the_owner_because_dbt_debug_prints_the_user(tmp_path: Path) -> None:
    result = make_run("-n", "dbt-debug", directory=tmp_path, shell={})
    assert result.returncode == 0, result.stderr
    assert "WAREHOUSE_TEST" not in result.stdout


def test_no_recipe_expands_either_value_in_make() -> None:
    """A recipe names the values only for the shell ($${NAME}) or for docker (-e NAME)."""
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    expanded = re.findall(r"(?<!\$)\$[({]WAREHOUSE_TEST_(?:USER|PASSWORD)[)}:]", makefile)
    assert expanded == []


def test_a_shell_value_wins_over_a_dotenv_that_sets_one(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "WAREHOUSE_TEST_USER=from_file\nWAREHOUSE_TEST_PASSWORD=file_secret\n", encoding="utf-8"
    )
    shell = {"WAREHOUSE_TEST_USER": INVENTED_USER, "WAREHOUSE_TEST_PASSWORD": INVENTED_PASSWORD}
    check = values_are(INVENTED_USER, INVENTED_PASSWORD)
    result = make_run("--eval", check, "values-are", directory=tmp_path, shell=shell)
    assert result.returncode == 0
    assert result.stdout == ""


def test_the_main_checkouts_dotenv_still_supplies_them_alone(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "WAREHOUSE_TEST_USER=from_file\nWAREHOUSE_TEST_PASSWORD=file_secret\n", encoding="utf-8"
    )
    check = values_are("from_file", "file_secret")
    result = make_run("--eval", check, "values-are", directory=tmp_path, shell={})
    assert result.returncode == 0
    assert result.stdout == ""


def test_every_copy_of_the_recipe_is_the_same_line() -> None:
    found = recipe_lines()
    assert all(len(lines) == 1 for lines in found.values()), {k: len(v) for k, v in found.items()}
    assert len({lines[0] for lines in found.values()}) == 1


def test_the_recipe_has_no_command_that_prints() -> None:
    (line,) = recipe_lines()["README.md"]
    assert PRINTING.findall(line) == []
    assert line.count("export ") == 1
    assert 'export "$line"' in line
    assert "'^WAREHOUSE_TEST_(USER|PASSWORD)='" in line


def test_the_printing_check_would_catch_each_form() -> None:
    probes = [
        'echo "$line"',
        "printf '%s' x",
        "env | grep X",
        "printenv X",
        "set -x",
        "export -p",
        "declare -p X",
        "cat .env",
        "x | tee log",
    ]
    assert [probe for probe in probes if not PRINTING.search(probe)] == []


def test_the_recipe_exports_only_the_two_lines_and_prints_nothing(tmp_path: Path) -> None:
    """The README recipe on an invented .env: two names exported, no output, no key."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not installed")
    (line,) = recipe_lines()["README.md"]
    main = tmp_path / "main"
    main.mkdir()
    (main / ".env").write_text(
        "WAREHOUSE_USER=owner\r\nNODE_ID_HMAC_KEY="
        + "ab" * 32
        + f"\r\nWAREHOUSE_TEST_USER={INVENTED_USER}\r\n"
        f"WAREHOUSE_TEST_PASSWORD={INVENTED_PASSWORD}\r\n",
        encoding="utf-8",
        newline="",
    )
    exported = (
        f'test "$WAREHOUSE_TEST_USER" = {INVENTED_USER} '
        f'&& test "$WAREHOUSE_TEST_PASSWORD" = {INVENTED_PASSWORD} '
        '&& test -z "${NODE_ID_HMAC_KEY+set}" && test -z "${WAREHOUSE_USER+set}"'
    )
    script = line.replace(MAIN_DOTENV, (main / ".env").as_posix()) + "; " + exported
    result = subprocess.run(
        [bash, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": os.environ.get("PATH", "")},
    )
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
