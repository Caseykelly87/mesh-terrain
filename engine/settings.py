"""Environment settings of the engine command, read once at startup.

These say where things are on this machine or in this image, never what a run
computes: every run parameter is in the config file (`docs/INTERFACE.md`).
The engine reads the environment only, not a `.env` file. Under
`make engine` the container's environment is the `engine` Compose service's
(`SNAPSHOTS_DIR`, `LOG_FORMAT`) and the image's ENV (`ITM_SOURCE_COMMIT`,
`ENGINE_GIT_COMMIT`); no `.env` variable reaches its environment. It needs
no database, so it does not use `pipeline.common.settings`, whose warehouse
fields are required.
"""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class EngineSettings(BaseSettings):
    """Where the engine finds snapshots, the ITM library and its own commit.

    Attributes:
        snapshots_dir: Folder whose subfolders are the snapshot ids a config
            names (`SNAPSHOTS_DIR`); `tests/fixtures/snapshots` for the fixture.
            Required: no default hides in code (`docs/INTERFACE.md`), so the
            command refuses to start without it.
        itm_library_path: NTIA's libitm.so (`ITM_LIBRARY_PATH`, ADR 0002).
        itm_source_commit: The NTIA ITM commit the library was built from
            (`ITM_SOURCE_COMMIT`), which the Dagster image records; required
            when a run lists itm.
        engine_git_commit: The engine's commit (`ENGINE_GIT_COMMIT`), for a
            place with no git checkout such as the image; a checkout's own
            `git rev-parse HEAD` wins over it.
        log_format: "console" or "json" (`LOG_FORMAT`), as the pipeline's.
    """

    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    snapshots_dir: Path
    itm_library_path: Path = Path("/usr/local/lib/libitm.so")
    itm_source_commit: str | None = None
    engine_git_commit: str | None = None
    log_format: Literal["console", "json"] = "console"
