"""make reads .env when it starts, so a .env that its env step creates is not read by that run.

A first `make check` in a checkout without .env used to create it and go on
with none of its variables exported, and every test that builds the settings
failed on missing fields (PR #97). The env step now stops such a run after
creating .env, so the next run reads it; `make env` on its own still creates
it and succeeds.

Each run uses the checkout's Makefile from a temporary folder holding a copy
of .env.example, so the checkout's own .env is never read or written.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
# What a calling make passes down, which would change what these runs see.
PASSED_DOWN = {"MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES"}
# A goal that needs the environment, standing in for check, test-role or up,
# whose recipes would reach for Docker.
PROBE = "probe: env\n\t@echo probe ran\n"


def make_in(folder: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """make with the checkout's Makefile and a probe goal, run in folder."""
    make = shutil.which("make")
    if make is None:
        pytest.skip("make is not installed")
    probe = folder / "probe.mk"
    probe.write_text(PROBE, encoding="utf-8")
    environment = {k: v for k, v in os.environ.items() if k not in PASSED_DOWN}
    return subprocess.run(
        [make, "--no-print-directory", "-f", str(REPO_ROOT / "Makefile"), "-f", str(probe)]
        + list(arguments),
        cwd=folder,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


@pytest.fixture
def fresh_checkout(tmp_path: Path) -> Path:
    shutil.copy(REPO_ROOT / ".env.example", tmp_path / ".env.example")
    return tmp_path


def test_a_goal_stops_after_its_env_step_creates_dotenv(fresh_checkout: Path) -> None:
    result = make_in(fresh_checkout, "probe")
    assert result.returncode != 0
    assert "probe ran" not in result.stdout
    assert "run make again" in result.stderr
    assert (fresh_checkout / ".env").is_file()


def test_the_next_run_reads_the_new_dotenv_and_goes_on(fresh_checkout: Path) -> None:
    make_in(fresh_checkout, "probe")
    result = make_in(fresh_checkout, "probe")
    assert result.returncode == 0, result.stderr
    assert "probe ran" in result.stdout


def test_make_env_alone_creates_dotenv_and_succeeds(fresh_checkout: Path) -> None:
    result = make_in(fresh_checkout, "env")
    assert result.returncode == 0, result.stderr
    assert (fresh_checkout / ".env").is_file()


def test_an_existing_dotenv_is_left_alone(fresh_checkout: Path) -> None:
    (fresh_checkout / ".env").write_text("LOG_FORMAT=console\n", encoding="utf-8")
    result = make_in(fresh_checkout, "probe")
    assert result.returncode == 0, result.stderr
    assert (fresh_checkout / ".env").read_text(encoding="utf-8") == "LOG_FORMAT=console\n"
