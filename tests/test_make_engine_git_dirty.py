"""The Makefile's ENGINE_GIT_DIRTY, which `make engine` passes into the engine container.

Each test makes a throwaway git repository (or a folder outside any) and asks
make, reading the repository's Makefile, for the variable's value there. It is
`true` when the checkout has tracked changes or untracked files, whatever
`status.showUntrackedFiles` says, `false` when it has only ignored files or
none, and empty outside a checkout, where the engine then refuses to run
(Casey, issue #55 comment 6961809084, item 11). Nothing starts a container.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GOAL = "print-engine-git-dirty"
PRINT_RULE = f'{GOAL}: ; @echo "[$(ENGINE_GIT_DIRTY)]"'
PASSED_DOWN = {"MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "ENGINE_GIT_DIRTY"}


def engine_git_dirty(folder: Path) -> str:
    """The value the Makefile gives ENGINE_GIT_DIRTY when make runs in `folder`."""
    make = shutil.which("make")
    if make is None:
        pytest.skip("make is not installed")
    environment = {k: v for k, v in os.environ.items() if k not in PASSED_DOWN}
    environment = {k: v for k, v in environment.items() if not k.startswith("GIT_")}
    # git must not climb out of the folder into a checkout that holds it.
    environment["GIT_CEILING_DIRECTORIES"] = str(folder.parent)
    result = subprocess.run(
        [make, "-s", "--no-print-directory", "-o", "env", "-f", str(REPO_ROOT / "Makefile")]
        + ["--eval", PRINT_RULE, GOAL],
        cwd=folder,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def git(folder: Path, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(folder), *arguments], check=True, capture_output=True)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """A git repository with one committed file and `.env` ignored through `.git/info/exclude`."""
    folder = tmp_path / "checkout"
    folder.mkdir()
    git(folder, "init", "--quiet")
    (folder / ".git" / "info" / "exclude").write_text(".env\n", encoding="utf-8")
    (folder / "tracked.py").write_text("ONE = 1\n", encoding="utf-8")
    git(folder, "add", "tracked.py")
    git(
        folder,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "one",
    )
    return folder


def test_a_clean_checkout_is_false(repository: Path) -> None:
    assert engine_git_dirty(repository) == "[false]"


def test_a_modified_tracked_file_is_true(repository: Path) -> None:
    (repository / "tracked.py").write_text("ONE = 2\n", encoding="utf-8")
    assert engine_git_dirty(repository) == "[true]"


def test_an_ignored_file_alone_is_still_false(repository: Path) -> None:
    (repository / ".env").write_text("INVENTED=1\n", encoding="utf-8")
    assert engine_git_dirty(repository) == "[false]"


def test_an_untracked_file_is_true(repository: Path) -> None:
    (repository / "untracked.py").write_text("", encoding="utf-8")
    assert engine_git_dirty(repository) == "[true]"


def test_an_untracked_file_is_true_when_git_is_set_to_hide_them(repository: Path) -> None:
    """The image is built from the working tree, so an untracked file is in it."""
    git(repository, "config", "status.showUntrackedFiles", "no")
    (repository / "untracked.py").write_text("", encoding="utf-8")
    assert engine_git_dirty(repository) == "[true]"


def test_outside_a_checkout_it_is_empty(tmp_path: Path) -> None:
    folder = tmp_path / "no-checkout"
    folder.mkdir()
    assert engine_git_dirty(folder) == "[]"
