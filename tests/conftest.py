"""Shared test setup: no test ever sees the real NODE_ID_HMAC_KEY (Casey, 2026-11-13).

Every test runs with the key removed from the environment and with the
ingester settings told not to read the repository's .env, so a test can only
use the invented keys it passes in itself.
"""

from collections.abc import Iterator

import pytest

from ingester.settings import IngesterSettings, get_ingester_settings


@pytest.fixture(autouse=True)
def no_real_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Hide the key from the environment and from the settings' .env file."""
    monkeypatch.delenv("NODE_ID_HMAC_KEY", raising=False)
    monkeypatch.setitem(IngesterSettings.model_config, "env_file", None)
    get_ingester_settings.cache_clear()
    yield
    get_ingester_settings.cache_clear()
