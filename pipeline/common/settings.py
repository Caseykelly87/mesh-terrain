"""Typed settings for the pipeline, read once from the environment.

This is the only module that reads environment variables. Everything else takes a
Settings instance or a value from it. It deliberately holds no secrets of its own:
values come from .env locally and from the container environment or SSM in cloud.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


def blank_as_none(value: object) -> object:
    """None for an empty or blank string, the value unchanged otherwise.

    An empty folder would otherwise become Path("."), the folder a command
    happens to start in.
    """
    if isinstance(value, str) and not value.strip():
        return None
    return value


def folder_in_checkout(folder: Path) -> Path:
    """The folder as given when it is absolute or rooted, else resolved under the checkout.

    make and Compose read a relative folder in .env from the checkout; a
    command started from another folder must read the same one.

    Args:
        folder: A folder from the environment or .env, in the host's path form.
    """
    return folder if folder.anchor else (REPO_ROOT / folder).resolve()


def optional_folder_in_checkout(folder: Path | None) -> Path | None:
    """folder_in_checkout for a folder that may be unset; None stays None."""
    return None if folder is None else folder_in_checkout(folder)


def refuse_blank(value: object) -> object:
    """The value unchanged, for a required folder.

    Raises:
        ValueError: if it is an empty or blank string.
    """
    if blank_as_none(value) is None:
        raise ValueError("is empty; set it to a folder")
    return value


class Settings(BaseSettings):
    """Environment-backed configuration shared by every asset and resource.

    Field names map to upper-case environment variables (warehouse_host is
    WAREHOUSE_HOST). A .env file at the repository root is read when present;
    real environment variables win over it.

    Raises:
        pydantic.ValidationError: at construction when a required variable is
            missing or has the wrong type.
    """

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Connection and credential fields have no defaults so a missing variable
    # fails at startup instead of silently pointing at the wrong place.
    warehouse_host: str
    warehouse_port: int = 5433
    warehouse_user: str
    warehouse_password: SecretStr
    warehouse_db: str

    # The tests' database role (docker/warehouse/test-role.sql); only the
    # tests read it, so the containers run without it.
    warehouse_test_user: str | None = None
    warehouse_test_password: SecretStr | None = None

    dagster_pg_db: str

    s3_endpoint_url: str
    minio_root_user: str
    minio_root_password: SecretStr
    raw_bucket: str

    log_format: Literal["console", "json"] = "console"

    dbt_project_dir: Path = REPO_ROOT / "dbt"

    # Where finished and .partial snapshot folders are (snp_01, pipeline.common.snapshot):
    # a host folder outside every Docker volume (ADR 0005 item 1), and /snapshots,
    # a read-only mount of it, in the containers. No default, as for the
    # connection: any default would name one machine's folder.
    snapshots_dir: Path

    _refuse_blank_snapshots_dir = field_validator("snapshots_dir", mode="before")(refuse_blank)
    _snapshots_dir_in_checkout = field_validator("snapshots_dir")(folder_in_checkout)

    # Where the Dagster image puts the NTIA ITM shared library (ADR 0002).
    # terrain/itm.py loads it through ctypes; override for a host-side build.
    itm_library_path: Path = Path("/usr/local/lib/libitm.so")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings, built on first call."""
    return Settings()
