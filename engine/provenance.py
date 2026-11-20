"""The code a run came from, for its manifest (Casey, PR #32).

Six manifest fields (manifest_version 1.1.0) trace a run to exact code:

- `engine_git_commit`, `engine_git_dirty`: the checkout's `git rev-parse HEAD`
  and whether `git status --porcelain` lists anything (tracked changes or
  untracked files), leaving out the run's own output folder when it lies
  inside the checkout, so a run written there does not mark the next one
  dirty. Where there is no checkout, as in the image, both come from the
  environment with a warning: `ENGINE_GIT_COMMIT` and `ENGINE_GIT_DIRTY`,
  which must be exactly `true` or `false` (`make engine` passes the host
  checkout's status). Since manifest 1.3.1 `engine_git_dirty` is never null:
  a run whose status is unknown is refused (Casey, issue #55 comment
  6961809084, item 11).
- `itm_library_sha256`, `itm_source_commit`: the sha256 of the file at
  `ITM_LIBRARY_PATH` and the NTIA commit it was built from
  (`ITM_SOURCE_COMMIT`, which the image records); null when the run does not
  list itm.
- `py1812_commit`, `py1812_archive_sha256`: the commit in Py1812's archive URL
  and the archive's sha256, both from `uv.lock`, after checking the installed
  package came from that URL and, when its `direct_url.json` records a hash,
  that the hash matches; null when the run does not list p1812. uv records
  none, which is logged at info, since `uv sync --frozen` checked the archive
  against `uv.lock` when it installed it.

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
from typing import Any

import structlog

from engine.errors import ConfigError
from engine.settings import EngineSettings
from terrain.path_loss import Model

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMIT = re.compile(r"[0-9a-f]{40}")
PY1812_ARCHIVE = re.compile(r"/archive/([0-9a-f]{40})\.tar\.gz$")
DIRTY = {"true": True, "false": False}

log = structlog.get_logger(component="engine.provenance")


@dataclass(frozen=True, kw_only=True)
class Provenance:
    """The code provenance fields of a run manifest; see the module docstring."""

    engine_git_commit: str
    engine_git_dirty: bool
    itm_library_sha256: str | None
    itm_source_commit: str | None
    py1812_commit: str | None
    py1812_archive_sha256: str | None


def collect_provenance(
    models: tuple[Model, ...], settings: EngineSettings, output_dir: Path | None = None
) -> Provenance:
    """Find the commit of the engine and of every model library the run uses.

    Args:
        models: The run's models.
        settings: Where the ITM library and the fallback commit are.
        output_dir: The run's output folder, left out of the dirty check.

    Raises:
        ConfigError: when the engine's commit is unknown, itm is listed and
            the library or its source commit is missing, or p1812 is listed
            and `uv.lock` or the installed Py1812 cannot be matched.
    """
    commit, dirty = engine_commit(settings, output_dir)
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


def engine_commit(settings: EngineSettings, output_dir: Path | None = None) -> tuple[str, bool]:
    """The engine's commit and whether the checkout differs from it.

    Raises:
        ConfigError: when there is no checkout and the environment does not
            give both a 40-character commit and a status of true or false.
    """
    try:
        head = _git("rev-parse", "HEAD").strip()
        status = _git("status", "--porcelain", "--", ".", *_excluded(output_dir))
    except (OSError, subprocess.CalledProcessError):
        return _commit_from_environment(settings)
    return head, bool(status.strip())


def _commit_from_environment(settings: EngineSettings) -> tuple[str, bool]:
    """The commit and status `make engine` passed, where there is no checkout to ask."""
    commit = settings.engine_git_commit
    if not commit or not COMMIT.fullmatch(commit):
        raise ConfigError(
            "the engine's commit is unknown: no git checkout, and ENGINE_GIT_COMMIT is not a "
            "40-character commit; the image sets it at build"
        )
    dirty = DIRTY.get(settings.engine_git_dirty or "")
    if dirty is None:
        raise ConfigError(
            "the engine's checkout status is unknown: no git checkout, and ENGINE_GIT_DIRTY is "
            f"{settings.engine_git_dirty!r}, not true or false; make engine passes it"
        )
    log.warning(
        "engine_commit_from_environment", reason="no git checkout", commit=commit, dirty=dirty
    )
    return commit, dirty


def _excluded(output_dir: Path | None) -> list[str]:
    """A git pathspec leaving out the output folder when it lies inside the checkout."""
    if output_dir is None:
        return []
    try:
        inside = output_dir.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return []
    return [f":(exclude){inside.as_posix()}"]


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


def py1812_pin(lock_path: Path, direct_url: dict[str, Any] | None = None) -> tuple[str, str]:
    """Py1812's commit and archive sha256 from `uv.lock`, checked against the installed package.

    Args:
        lock_path: The project's `uv.lock`.
        direct_url: The installed package's `direct_url.json`; read from the
            installed metadata when None.

    Raises:
        ConfigError: if the lock has no Py1812 archive entry, or the installed
            package came from another URL or records another archive hash.
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
    installed = direct_url if direct_url is not None else _installed_direct_url()
    if installed.get("url") != url:
        raise ConfigError(f"the installed Py1812 came from {installed.get('url')!r}, not {url}")
    sha256 = archive_hash.removeprefix("sha256:")
    _check_installed_hash(installed, sha256)
    return match.group(1), sha256


def _installed_direct_url() -> dict[str, Any]:
    try:
        distribution = metadata.distribution("py1812")
    except metadata.PackageNotFoundError as err:
        raise ConfigError(
            "p1812 is listed but Py1812 is not installed; uv sync --frozen installs it"
        ) from err
    text = distribution.read_text("direct_url.json")
    installed: dict[str, Any] = json.loads(text or "{}")
    return installed


def _check_installed_hash(installed: dict[str, Any], lock_sha256: str) -> None:
    """Compare the installed archive hash with the lock's, where the installer recorded one."""
    archive_info = installed.get("archive_info", {})
    recorded = archive_info.get("hashes", {}).get("sha256")
    legacy = str(archive_info.get("hash", ""))
    if recorded is None and legacy.startswith("sha256="):
        recorded = legacy.removeprefix("sha256=")
    if recorded is None:
        # Info, not a warning (Casey, PR #43): uv never records a hash here,
        # so this is every uv install, and the lock check already covered it.
        log.info(
            "py1812_installed_hash_unrecorded",
            reason="uv records no hash in direct_url.json",
            checked_by="uv sync --frozen checked the archive against uv.lock at install",
            lock_sha256=lock_sha256,
        )
        return
    if recorded != lock_sha256:
        raise ConfigError(
            f"the installed Py1812 archive has sha256 {recorded}, uv.lock has {lock_sha256}"
        )
