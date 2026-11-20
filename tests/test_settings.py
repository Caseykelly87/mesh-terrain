"""Settings: the key is a secret of 32 bytes as 64 hex characters; SNAPSHOTS_DIR is read by both.

No folder has a default: a default would have to name one machine's folders,
and a clone elsewhere would read or write the wrong place without a word.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import BaseModel, SecretStr, ValidationError

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
SNAPSHOTS = Path("snapshots")
CAPTURE_VARIABLES = ("PUBLIC_CAPTURE_DIR", "MESHSTL_CAPTURE_DIR")


def test_the_key_decodes_to_32_bytes() -> None:
    settings = IngesterSettings(
        _env_file=None, snapshots_dir=SNAPSHOTS, node_id_hmac_key=SecretStr(KEY_HEX)
    )
    assert settings.hmac_key() == bytes(range(32))


def test_a_missing_key_stops_the_commands_that_pseudonymise() -> None:
    with pytest.raises(MissingKeyError):
        IngesterSettings(_env_file=None, snapshots_dir=SNAPSHOTS, node_id_hmac_key=None).hmac_key()


def test_the_key_never_appears_in_the_settings_repr() -> None:
    settings = IngesterSettings(
        _env_file=None, snapshots_dir=SNAPSHOTS, node_id_hmac_key=SecretStr(KEY_HEX)
    )
    assert KEY_HEX not in repr(settings)
    assert KEY_HEX not in str(settings.model_dump())


def test_the_pipeline_settings_read_snapshots_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SNAPSHOTS_DIR", str(tmp_path))
    assert Settings(_env_file=None, **PIPELINE_FIELDS).snapshots_dir == tmp_path
    assert IngesterSettings(_env_file=None).snapshots_dir == tmp_path


@pytest.mark.parametrize(
    "build",
    [
        lambda: Settings(_env_file=None, **PIPELINE_FIELDS),
        lambda: IngesterSettings(_env_file=None),
    ],
    ids=["pipeline", "ingester"],
)
def test_an_unset_snapshots_dir_is_refused(
    build: Callable[[], BaseModel], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SNAPSHOTS_DIR", raising=False)
    with pytest.raises(ValidationError, match="snapshots_dir"):
        build()


def test_the_capture_folders_are_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PUBLIC_CAPTURE_DIR", str(tmp_path / "public"))
    monkeypatch.setenv("MESHSTL_CAPTURE_DIR", str(tmp_path / "meshstl"))
    settings = IngesterSettings(_env_file=None, snapshots_dir=SNAPSHOTS)
    assert settings.public_capture_dir == tmp_path / "public"
    assert settings.meshstl_capture_dir == tmp_path / "meshstl"


def test_the_capture_folders_have_no_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in CAPTURE_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    settings = IngesterSettings(_env_file=None, snapshots_dir=SNAPSHOTS)
    assert (settings.public_capture_dir, settings.meshstl_capture_dir) == (None, None)
