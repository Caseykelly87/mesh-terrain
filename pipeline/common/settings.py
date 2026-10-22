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
    """

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    warehouse_host: str = "localhost"
    warehouse_port: int = 5433
    warehouse_user: str = "mesh"
    warehouse_password: SecretStr = SecretStr("mesh")
    warehouse_db: str = "mesh_terrain"

    dagster_pg_db: str = "dagster"

    s3_endpoint_url: str = "http://localhost:9000"
    minio_root_user: str = "minio"
    minio_root_password: SecretStr = SecretStr("minio12345")
    raw_bucket: str = "mesh-terrain-raw"

    log_format: Literal["console", "json"] = "console"

    dbt_project_dir: Path = REPO_ROOT / "dbt"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings, built on first call."""
    return Settings()
