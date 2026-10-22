"""Typed settings for the pipeline, read once from the environment.

This is the only module that reads environment variables. Everything else takes a
Settings instance or a value from it. It deliberately holds no secrets of its own:
values come from .env locally and from the container environment or SSM in cloud.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


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

    dagster_pg_db: str

    s3_endpoint_url: str
    minio_root_user: str
    minio_root_password: SecretStr
    raw_bucket: str

    log_format: Literal["console", "json"] = "console"

    dbt_project_dir: Path = REPO_ROOT / "dbt"

    # Where the Dagster image puts the NTIA ITM shared library (ADR 0002).
    # terrain/itm.py loads it through ctypes; override for a host-side build.
    itm_library_path: Path = Path("/usr/local/lib/libitm.so")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings, built on first call."""
    return Settings()
