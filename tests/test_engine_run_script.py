"""docker/engine-run.sh, the checks `make engine` runs before any image build.

The script runs with ENGINE_COMPOSE=echo, so Docker never starts: a refusal
exits 2 with a "make engine: refused" line, and an accepted run prints the
build and run commands it would have run, in that order.
"""

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "docker" / "engine-run.sh"
FIXTURE_CONFIG = REPO / "engine" / "fixtures" / "fixture-run-itm.yaml"
SNAPSHOTS = REPO / "tests" / "fixtures" / "snapshots"


def find_bash() -> str | None:
    """Git Bash on Windows, any bash elsewhere, or None.

    From PowerShell, `bash` on PATH can be the WSL launcher in System32, which
    runs the script in another file system where these paths do not exist.
    """
    bash = shutil.which("bash")
    if os.name != "nt":
        return bash
    if bash is not None and not Path(bash).parent.as_posix().lower().endswith(
        ("/system32", "/windowsapps")
    ):
        return bash
    git_bash = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git" / "bin" / "bash.exe"
    return str(git_bash) if git_bash.is_file() else None


BASH = find_bash()

pytestmark = pytest.mark.skipif(
    BASH is None, reason="docker/engine-run.sh needs bash (Git Bash on Windows)"
)


def main_checkout() -> Path:
    """The main checkout's folder, which is REPO itself outside a worktree."""
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return Path(common).parent


def run_script(
    config: Path, runs: Path | str, env: dict[str, str], cwd: Path = REPO
) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in {"SNAPSHOTS_DIR", "SNAPSHOTS_HOST_DIR"}
    }
    environment.update(ENGINE_COMPOSE="echo", SNAPSHOTS_HOST_DIR=SNAPSHOTS.as_posix())
    environment.update(env)
    runs_arg = runs.as_posix() if isinstance(runs, Path) else runs
    return subprocess.run(
        [BASH, SCRIPT.as_posix(), config.as_posix(), runs_arg],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def config_with(tmp_path: Path, output_line: str, extra: str = "") -> Path:
    """A copy of the itm fixture config whose output_dir line is replaced."""
    text = FIXTURE_CONFIG.read_text(encoding="utf-8")
    assert "\noutput_dir: /runs\n" in text
    folder = tmp_path / "cfg"
    folder.mkdir(exist_ok=True)
    config = folder / "run.yaml"
    config.write_text(
        text.replace("output_dir: /runs\n", output_line + "\n") + extra, encoding="utf-8"
    )
    return config


def with_file(tmp_path: Path, relative: str) -> Path:
    """The fixture config copied into a folder that also holds `relative`."""
    config = config_with(tmp_path, "output_dir: /runs")
    marker = config.parent / relative
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("NOT_A_SECRET=1\n", encoding="utf-8")
    return config


def made(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# Each case returns (config, runs folder, extra environment) from tmp_path,
# whose "runs" folder exists, and names a fragment of the refusal line.
Case = Callable[[Path], tuple[Path, Path | str, dict[str, str]]]

REFUSALS: dict[str, tuple[Case, str]] = {
    "missing config": (
        lambda t: (t / "none.yaml", t / "runs", {}),
        "must name an engine config file",
    ),
    "empty runs": (lambda t: (FIXTURE_CONFIG, "", {}), "RUNS_HOST_DIR is empty"),
    "missing runs": (lambda t: (FIXTURE_CONFIG, t / "nope", {}), "is not an existing folder"),
    "empty snapshots": (
        lambda t: (FIXTURE_CONFIG, t / "runs", {"SNAPSHOTS_HOST_DIR": ""}),
        "SNAPSHOTS_HOST_DIR is empty",
    ),
    "missing snapshots": (
        lambda t: (FIXTURE_CONFIG, t / "runs", {"SNAPSHOTS_HOST_DIR": (t / "no-snaps").as_posix()}),
        "SNAPSHOTS_HOST_DIR",
    ),
    "output_dir outside /runs": (
        lambda t: (config_with(t, "output_dir: /tmp/out"), t / "runs", {}),
        "output_dir must be /runs",
    ),
    "output_dir /runsx": (
        lambda t: (config_with(t, "output_dir: /runsx"), t / "runs", {}),
        "output_dir must be /runs",
    ),
    "output_dir with a .. segment": (
        lambda t: (config_with(t, "output_dir: /runs/../tmp"), t / "runs", {}),
        "..",
    ),
    "output_dir ending in ..": (
        lambda t: (config_with(t, 'output_dir: "/runs/a/.." # up'), t / "runs", {}),
        "..",
    ),
    # YAML reads a # with no space before it as part of the value.
    "output_dir with a # in the value": (
        lambda t: (config_with(t, "output_dir: /runs#x"), t / "runs", {}),
        "output_dir must be /runs",
    ),
    # A double-quoted YAML string turns \x2e into a dot, so this is /runs/../etc.
    "output_dir with an escape": (
        lambda t: (config_with(t, 'output_dir: "/runs/\\x2e\\x2e/etc"'), t / "runs", {}),
        "output_dir must be /runs",
    ),
    "second output_dir line": (
        lambda t: (config_with(t, "output_dir: /runs", "output_dir: /tmp/out\n"), t / "runs", {}),
        "once",
    ),
    "second output_dir as a quoted key": (
        lambda t: (config_with(t, "output_dir: /runs", '"output_dir": /tmp/out\n'), t / "runs", {}),
        "once",
    ),
    "output_dir missing": (
        lambda t: (config_with(t, "# no output_dir"), t / "runs", {}),
        "once",
    ),
    "runs is the checkout": (lambda t: (FIXTURE_CONFIG, REPO, {}), "checkout"),
    "runs inside the checkout": (lambda t: (FIXTURE_CONFIG, REPO / "docs", {}), "checkout"),
    "runs holds the checkout": (lambda t: (FIXTURE_CONFIG, REPO.parent, {}), "checkout"),
    "runs inside the main checkout": (
        lambda t: (FIXTURE_CONFIG, main_checkout() / "docs", {}),
        "checkout",
    ),
    "runs inside SNAPSHOTS_HOST_DIR": (
        lambda t: (
            FIXTURE_CONFIG,
            made(t / "snaps" / "inner"),
            {"SNAPSHOTS_HOST_DIR": (t / "snaps").as_posix()},
        ),
        "inside SNAPSHOTS_HOST_DIR",
    ),
    "runs holds SNAPSHOTS_DIR": (
        lambda t: (FIXTURE_CONFIG, t, {"SNAPSHOTS_DIR": (t / "runs").as_posix()}),
        "holds SNAPSHOTS_DIR",
    ),
    "config at the checkout root": (
        lambda t: (REPO / "pyproject.toml", t / "runs", {}),
        "checkout",
    ),
    "config at the main checkout root": (
        lambda t: (main_checkout() / "pyproject.toml", t / "runs", {}),
        "checkout",
    ),
    "config folder holds .env": (lambda t: (with_file(t, ".env"), t / "runs", {}), ".env"),
    "config folder holds meshstl.env below it": (
        lambda t: (with_file(t, "sub/meshstl.env"), t / "runs", {}),
        "meshstl.env",
    ),
}


@pytest.mark.parametrize("name", list(REFUSALS))
def test_refused_before_any_build(tmp_path: Path, name: str) -> None:
    case, fragment = REFUSALS[name]
    (tmp_path / "runs").mkdir()
    config, runs, env = case(tmp_path)
    result = run_script(config, runs, env)
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == "", "a refused run must not reach the build"
    assert result.stderr.startswith("make engine: refused: ")
    assert fragment in result.stderr


def test_config_folder_holding_a_checkout_is_refused(tmp_path: Path) -> None:
    folder = made(tmp_path / "configs")
    checkout = made(folder / "checkout")
    subprocess.run(["git", "init", "--quiet", checkout.as_posix()], check=True)
    config = folder / "run.yaml"
    config.write_text(FIXTURE_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    result = run_script(config, made(tmp_path / "runs"), {}, cwd=checkout)
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == ""
    assert "is or holds the checkout" in result.stderr


def fold(text: str) -> str:
    """Windows paths compare without case, as the script compares them."""
    return text.lower() if os.name == "nt" else text


@pytest.mark.parametrize(
    "output_line", ["output_dir: /runs", 'output_dir: "/runs/sub" # a run\'s own folder']
)
def test_accepted_run_builds_then_runs(tmp_path: Path, output_line: str) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    config = config_with(tmp_path, output_line)
    result = run_script(config, runs, {})
    assert result.returncode == 0, result.stderr
    build, run = result.stdout.splitlines()
    assert build == "--profile engine build engine"
    assert run.startswith("--profile engine run --rm --no-deps -T --user ")
    assert fold(f"--volume {config.parent.resolve().as_posix()}:/config:ro ") in fold(run)
    assert fold(f"--volume {runs.resolve().as_posix()}:/runs ") in fold(run)
    assert run.endswith(" engine python -m engine run /config/run.yaml")
