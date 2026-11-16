"""The code a run came from, for its manifest (Casey, PR #32).

Six manifest fields (manifest_version 1.1.0) trace a run to exact code:

- `engine_git_commit`, `engine_git_dirty`: the checkout's `git rev-parse HEAD`
  and whether `git status --porcelain` lists anything (tracked changes or
  untracked files). Where there is no checkout, as in the image, the commit
  comes from `ENGINE_GIT_COMMIT` and `engine_git_dirty` is null (unknown).
- `itm_library_sha256`, `itm_source_commit`: the sha256 of the file at
  `ITM_LIBRARY_PATH` and the NTIA commit it was built from
  (`ITM_SOURCE_COMMIT`, which the image records); null when the run does not
  list itm.
- `py1812_commit`, `py1812_archive_sha256`: the commit in Py1812's archive URL
  and the archive's sha256, both from `uv.lock`, after checking the installed
  package came from that URL; null when the run does not list p1812.

A run that would need a value it cannot find is refused before it starts.
"""

import hashlib
import json
import re
import subprocess
import tomllib
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from engine.errors import ConfigError
from engine.settings import EngineSettings
from terrain.path_loss import Model

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMIT = re.compile(r"[0-9a-f]{40}")
PY1812_ARCHIVE = re.compile(r"/archive/([0-9a-f]{40})\.tar\.gz$")


@dataclass(frozen=True, kw_only=True)
class Provenance:
    """The code provenance fields of a run manifest; see the module docstring."""

    engine_git_commit: str
    engine_git_dirty: bool | None
    itm_library_sha256: str | None
    itm_source_commit: str | None
    py1812_commit: str | None
    py1812_archive_sha256: str | None


def collect_provenance(models: tuple[Model, ...], settings: EngineSettings) -> Provenance:
    """Find the commit of the engine and of every model library the run uses.

    Raises:
        ConfigError: when the engine's commit is unknown, itm is listed and
            the library or its source commit is missing, or p1812 is listed
            and `uv.lock` or the installed Py1812 cannot be matched.
    """
    commit, dirty = engine_commit(settings)
    itm_sha256, itm_commit = _itm(settings) if "itm" in models else (None, None)
    py1812_commit, py1812_sha256 = (
        py1812_pin(REPO_ROOT / "uv.lock") if "p1812" in models else (None, None)
    )
    return Provenance(
        engine_git_commit=commit,
        engine_git_dirty=dirty,
        itm_library_sha256=itm_sha256,
        itm_source_commit=itm_commit,
        py1812_commit=py1812_commit,
        py1812_archive_sha256=py1812_sha256,
    )


def engine_commit(settings: EngineSettings) -> tuple[str, bool | None]:
    """The engine's commit and whether the checkout differs from it."""
    try:
        head = _git("rev-parse", "HEAD").strip()
        status = _git("status", "--porcelain")
    except (OSError, subprocess.CalledProcessError):
        if settings.engine_git_commit and COMMIT.fullmatch(settings.engine_git_commit):
            return settings.engine_git_commit, None
        raise ConfigError(
            "the engine's commit is unknown: no git checkout, and ENGINE_GIT_COMMIT is not a "
            "40-character commit; the image sets it at build"
        ) from None
    return head, bool(status.strip())


def _git(*arguments: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


def _itm(settings: EngineSettings) -> tuple[str, str]:
    path = settings.itm_library_path
    if not path.is_file():
        raise ConfigError(f"itm is listed but there is no ITM library at {path} (ITM_LIBRARY_PATH)")
    commit = settings.itm_source_commit
    if not commit or not COMMIT.fullmatch(commit):
        raise ConfigError(
            "itm is listed but ITM_SOURCE_COMMIT is not the 40-character NTIA commit the "
            "library was built from; the image records it"
        )
    return hashlib.sha256(path.read_bytes()).hexdigest(), commit


def py1812_pin(lock_path: Path) -> tuple[str, str]:
    """Py1812's commit and archive sha256 from `uv.lock`, checked against the installed package.

    Raises:
        ConfigError: if the lock has no Py1812 archive entry, or the installed
            package came from another URL.
    """
    if not lock_path.is_file():
        raise ConfigError(f"p1812 is listed but there is no uv.lock at {lock_path}")
    packages = tomllib.loads(lock_path.read_text(encoding="utf-8")).get("package", [])
    entry = next((p for p in packages if p.get("name") == "py1812"), None)
    url = (entry or {}).get("source", {}).get("url", "")
    match = PY1812_ARCHIVE.search(url)
    archive_hash = str((entry or {}).get("sdist", {}).get("hash", ""))
    if match is None or not archive_hash.startswith("sha256:"):
        raise ConfigError("uv.lock has no Py1812 archive with a sha256 (ADR 0006 decision 1)")
    installed = json.loads(metadata.distribution("py1812").read_text("direct_url.json") or "{}")
    if installed.get("url") != url:
        raise ConfigError(f"the installed Py1812 came from {installed.get('url')!r}, not {url}")
    return match.group(1), archive_hash.removeprefix("sha256:")
