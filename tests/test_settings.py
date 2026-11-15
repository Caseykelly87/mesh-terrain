"""Settings: the key is a secret of 32 bytes as 64 hex characters; SNAPSHOTS_DIR is read by both."""

from pathlib import Path

import pytest
from pydantic import SecretStr

from ingester.settings import IngesterSettings, MissingKeyError
from pipeline.common.settings import Settings

# Invented values for the pipeline settings' required fields.
PIPELINE_FIELDS = {
    "warehouse_host": "warehouse.example.invalid",
    "warehouse_user": "user",
    "warehouse_password": SecretStr("password"),
    "warehouse_db": "db",
    "dagster_pg_db": "dagster",
    "s3_endpoint_url": "http://s3.example.invalid",
    "minio_root_user": "user",
    "minio_root_password": SecretStr("password"),
    "raw_bucket": "raw",
}

KEY_HEX = bytes(range(32)).hex()


def test_the_key_decodes_to_32_bytes() -> None:
    settings = IngesterSettings(_env_file=None, node_id_hmac_key=SecretStr(KEY_HEX))
    assert settings.hmac_key() == bytes(range(32))


def test_a_missing_key_stops_the_commands_that_pseudonymise() -> None:
    with pytest.raises(MissingKeyError):
        IngesterSettings(_env_file=None, node_id_hmac_key=None).hmac_key()


def test_the_key_never_appears_in_the_settings_repr() -> None:
    settings = IngesterSettings(_env_file=None, node_id_hmac_key=SecretStr(KEY_HEX))
    assert KEY_HEX not in repr(settings)
    assert KEY_HEX not in str(settings.model_dump())


def test_the_pipeline_settings_read_snapshots_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SNAPSHOTS_DIR", str(tmp_path))
    assert Settings(_env_file=None, **PIPELINE_FIELDS).snapshots_dir == tmp_path
    assert IngesterSettings(_env_file=None).snapshots_dir == tmp_path


def test_both_settings_default_to_the_same_snapshots_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SNAPSHOTS_DIR", raising=False)
    pipeline_default = Settings(_env_file=None, **PIPELINE_FIELDS).snapshots_dir
    assert pipeline_default == Path("C:/Users/Casey/mesh-terrain-data/snapshots")
    assert IngesterSettings(_env_file=None).snapshots_dir == pipeline_default
