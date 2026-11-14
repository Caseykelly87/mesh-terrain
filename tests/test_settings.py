"""The key is read as a secret and refused unless it is 32 bytes written as 64 hex characters."""

import pytest
from pydantic import SecretStr

from ingester.settings import IngesterSettings, MissingKeyError

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
