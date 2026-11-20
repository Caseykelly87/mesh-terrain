"""A fresh clone in any folder runs from its own .env: no file names one machine's user folder.

The ADRs and their spike folders keep the paths they ran with, as records of
what was run, so they are not read here.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
READ = (
    ".env.example",
    "Makefile",
    "README.md",
    "docker-compose.yml",
    ".github",
    "docker",
    "docs",
    "dbt",
    "engine",
    "ingester",
    "pipeline",
    "terrain",
    "tests",
)
NOT_READ = (REPO_ROOT / "docs" / "adr",)
TEXT_SUFFIXES = {"", ".md", ".py", ".sh", ".sql", ".toml", ".yaml", ".yml", ".example"}
# A Windows user folder in either form, C:/Users/<name> or Git Bash's
# /c/Users/<name>. The placeholder name "you" marks an example, not a machine.
USER_FOLDER = re.compile(r"(?:\b[A-Za-z]:|(?<![\w.])/[a-z])[/\\]+Users[/\\]+(?!you\b)\w")


def text_files() -> list[Path]:
    """Every text file under READ, outside NOT_READ and outside generated folders."""
    found: list[Path] = []
    for name in READ:
        path = REPO_ROOT / name
        candidates = [path] if path.is_file() else path.rglob("*")
        found += [
            candidate
            for candidate in candidates
            if candidate.is_file()
            and candidate.suffix in TEXT_SUFFIXES
            and not any(candidate.is_relative_to(skipped) for skipped in NOT_READ)
            and not {"target", "dbt_packages", "logs", "__pycache__"} & set(candidate.parts)
        ]
    return found


def test_no_file_names_a_user_folder() -> None:
    named = [
        f"{path.relative_to(REPO_ROOT).as_posix()}:{number}"
        for path in text_files()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if USER_FOLDER.search(line)
    ]
    assert named == []


def test_the_user_folder_check_sees_both_forms_and_passes_the_placeholder() -> None:
    # Split, so this file does not name a user folder itself.
    name = "someone"
    probes = {
        f"C:/Users/{name}/data": True,
        f"c:\\Users\\{name}\\data": True,
        f"/c/Users/{name}/data": True,
        "C:/Users/you/.certs/ca-bundle.pem": False,
        "./data/snapshots": False,
        "/usr/local/lib/libitm.so": False,
    }
    assert {probe: bool(USER_FOLDER.search(probe)) for probe in probes} == probes
