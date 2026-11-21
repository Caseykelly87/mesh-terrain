"""A fresh clone in any folder runs from its own .env: no tracked file names a user folder.

The ADRs and their spike folders keep the paths they ran with, as records of
what was run, so they are not read here.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
NOT_READ = "docs/adr/"
BINARY_SUFFIXES = {".tif", ".parquet"}
# A user folder in any of its forms: C:/Users/<name> and C:\Users\<name> on
# Windows, /c/Users/<name> in Git Bash, /Users/<name> on macOS and
# /home/<name> on Linux. The placeholder name "you" marks an example.
USER_FOLDER = re.compile(
    r"(?:\b[A-Za-z]:[/\\]+|(?<![\w.~])/(?:[A-Za-z]/)?)(?:Users|home)[/\\]+(?!you\b)\w"
)


def tracked_text_files() -> list[Path]:
    """Every tracked file outside NOT_READ, except the binary ones."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed")
    listed = subprocess.run(
        [git, "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8")
    return [
        REPO_ROOT / name
        for name in listed.split("\0")
        if name
        and not name.startswith(NOT_READ)
        and Path(name).suffix.lower() not in BINARY_SUFFIXES
    ]


def test_no_tracked_file_names_a_user_folder() -> None:
    named = [
        f"{path.relative_to(REPO_ROOT).as_posix()}:{number}"
        for path in tracked_text_files()
        if path.is_file()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if USER_FOLDER.search(line)
    ]
    assert named == []


def test_the_user_folder_check_sees_every_form_and_passes_the_placeholder() -> None:
    # Split, so this file does not name a user folder itself.
    name = "someone"
    probes = {
        f"C:/Users/{name}/data": True,
        f"c:\\Users\\{name}\\data": True,
        f"/c/Users/{name}/data": True,
        f"/Users/{name}/data": True,
        f"/home/{name}/data": True,
        "C:/Users/you/.certs/ca-bundle.pem": False,
        "../mesh-terrain-data/snapshots": False,
        "/usr/local/lib/libitm.so": False,
        "~/.cache/uv": False,
    }
    assert {probe: bool(USER_FOLDER.search(probe)) for probe in probes} == probes


def test_a_file_that_is_not_utf8_text_is_skipped(tmp_path: Path) -> None:
    figure = tmp_path / "figure.png"
    figure.write_bytes(bytes([0x89, 0x50, 0x4E, 0x47, 0xFF, 0xFE]))
    assert text_lines(figure) == []
