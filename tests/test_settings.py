"""Settings: the key is a secret of 32 bytes as 64 hex characters; SNAPSHOTS_DIR is read by both.

No folder has a default: a default would have to name one machine's folders,
and a clone elsewhere would read or write the wrong place without a word. For
the same reason a relative folder is read from the checkout, as make and
Compose read it, never from the folder a command happens to start in, and an
empty one is refused.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
import raw_schema
from pydantic import BaseModel, SecretStr, ValidationError

from ingester.settings import IngesterSettings, MissingKeyError
from pipeline.common.settings import REPO_ROOT, Settings, get_settings

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
@pytest.mark.parametrize("value", [None, "", "  "], ids=["unset", "empty", "blank"])
def test_an_unset_or_empty_snapshots_dir_is_refused(
    build: Callable[[], BaseModel], value: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty value would otherwise be Path("."), the folder the command started in."""
    if value is None:
        monkeypatch.delenv("SNAPSHOTS_DIR", raising=False)
    else:
        monkeypatch.setenv("SNAPSHOTS_DIR", value)
    with pytest.raises(ValidationError, match="snapshots_dir"):
        build()


@pytest.mark.parametrize(
    "build",
    [
        lambda: Settings(_env_file=None, **PIPELINE_FIELDS).snapshots_dir,
        lambda: IngesterSettings(_env_file=None).snapshots_dir,
        lambda: IngesterSettings(_env_file=None, snapshots_dir=SNAPSHOTS).public_capture_dir,
        lambda: IngesterSettings(_env_file=None, snapshots_dir=SNAPSHOTS).meshstl_capture_dir,
    ],
    ids=["pipeline", "ingester", "public capture", "meshstl capture"],
)
def test_a_relative_folder_is_read_from_the_checkout_not_the_working_folder(
    build: Callable[[], Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in ("SNAPSHOTS_DIR", *CAPTURE_VARIABLES):
        monkeypatch.setenv(name, "../mesh-terrain-data/snapshots")
    monkeypatch.chdir(tmp_path)
    assert build() == (REPO_ROOT / ".." / "mesh-terrain-data" / "snapshots").resolve()


def test_an_absolute_folder_is_kept(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SNAPSHOTS_DIR", str(tmp_path))
    assert IngesterSettings(_env_file=None).snapshots_dir == tmp_path


def test_the_capture_folders_are_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PUBLIC_CAPTURE_DIR", str(tmp_path / "public"))
    monkeypatch.setenv("MESHSTL_CAPTURE_DIR", str(tmp_path / "meshstl"))
    settings = IngesterSettings(_env_file=None, snapshots_dir=SNAPSHOTS)
    assert settings.public_capture_dir == tmp_path / "public"
    assert settings.meshstl_capture_dir == tmp_path / "meshstl"


@pytest.mark.parametrize("value", [None, ""], ids=["unset", "empty"])
def test_an_unset_or_empty_capture_folder_is_none(
    value: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """None, so the reduction refuses it, rather than Path("."), the working folder."""
    for name in CAPTURE_VARIABLES:
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    settings = IngesterSettings(_env_file=None, snapshots_dir=SNAPSHOTS)
    assert (settings.public_capture_dir, settings.meshstl_capture_dir) == (None, None)


def test_a_test_role_lookup_that_fails_names_the_missing_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otherwise the warehouse tests fail on the role, far from the real cause."""
    monkeypatch.delenv("SNAPSHOTS_DIR", raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    try:
        with pytest.warns(UserWarning, match="SNAPSHOTS_DIR"):
            assert raw_schema.role_for_tests() is None
    finally:
        get_settings.cache_clear()
