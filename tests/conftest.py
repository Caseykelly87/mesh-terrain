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
no test role configured they name a role that does not exist, with a
placeholder password, so no test connects as the owner: where a warehouse
answers, it refuses that login and each test that connects through
`tests/raw_schema.py`'s `connect_or_skip` fails, saying what to set.

Neither value reaches a test or collection report: both are replaced in its
traceback, locals and captured output, at any verbosity (Casey, issue #55
comment 7017280290, item 2; `tests/report_redaction.py`).
"""

from collections.abc import Generator, Iterator
from typing import Any

import pytest
import report_redaction
from raw_schema import role_for_tests, session_credentials

from ingester.settings import IngesterSettings, get_ingester_settings
from pipeline.common.settings import get_settings


def pytest_configure(config: pytest.Config) -> None:
    """Point WAREHOUSE_USER and WAREHOUSE_PASSWORD at the test role for the whole session."""
    patch = pytest.MonkeyPatch()
    config.add_cleanup(patch.undo)
    config.add_cleanup(get_settings.cache_clear)
    config.add_cleanup(report_redaction.remember)
    role = role_for_tests()
    report_redaction.remember(*(role or ()))
    user, password = session_credentials(role)
    patch.setenv("WAREHOUSE_USER", user)
    patch.setenv("WAREHOUSE_PASSWORD", password)
    get_settings.cache_clear()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport() -> Generator[None, Any, None]:
    """Replace the test role's values in each setup, call and teardown report."""
    outcome = yield
    report_redaction.scrub(outcome.get_result())


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report() -> Generator[None, Any, None]:
    """Replace the test role's values in each collection report, an import error's included."""
    outcome = yield
    report_redaction.scrub(outcome.get_result())


@pytest.fixture(autouse=True)
def no_real_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Hide the key from the environment and from the settings' .env file."""
    monkeypatch.delenv("NODE_ID_HMAC_KEY", raising=False)
    monkeypatch.setitem(IngesterSettings.model_config, "env_file", None)
    get_ingester_settings.cache_clear()
    yield
    get_ingester_settings.cache_clear()
