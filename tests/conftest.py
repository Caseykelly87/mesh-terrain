"""Shared test setup: the real NODE_ID_HMAC_KEY is hidden, and the tests connect as the test role.

Every test runs with the key removed from the environment and with the
ingester settings told not to read the repository's .env, so a test can only
use the invented keys it passes in itself (Casey, 2026-11-13).

Every warehouse connection the tests open, the dbt runs they start included,
is made as the test role, which cannot write `raw`, `raw_dev_wh` or
`raw_dev_ta` (Casey, issue #55 comment 6961809084, item 10;
`docker/warehouse/test-role.sql`). WAREHOUSE_USER and WAREHOUSE_PASSWORD are
replaced in the environment before any test module is imported, so settings
built later, and dbt's profile in a subprocess, see only the test role. With
no test role configured they name a role that does not exist, so no test
connects as the owner: where a warehouse answers, it refuses that login and
each warehouse test fails, saying what to set (`tests/raw_schema.py`).
"""

from collections.abc import Iterator

import pytest
from raw_schema import UNCONFIGURED_ROLE, role_for_tests

from ingester.settings import IngesterSettings, get_ingester_settings
from pipeline.common.settings import get_settings


def pytest_configure(config: pytest.Config) -> None:
    """Point WAREHOUSE_USER and WAREHOUSE_PASSWORD at the test role for the whole session."""
    role = role_for_tests()
    patch = pytest.MonkeyPatch()
    config.add_cleanup(patch.undo)
    config.add_cleanup(get_settings.cache_clear)
    patch.setenv("WAREHOUSE_USER", role.user if role else UNCONFIGURED_ROLE)
    patch.setenv("WAREHOUSE_PASSWORD", role.password if role else "")
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def no_real_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Hide the key from the environment and from the settings' .env file."""
    monkeypatch.delenv("NODE_ID_HMAC_KEY", raising=False)
    monkeypatch.setitem(IngesterSettings.model_config, "env_file", None)
    get_ingester_settings.cache_clear()
    yield
    get_ingester_settings.cache_clear()
