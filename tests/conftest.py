"""Shared test setup: the real NODE_ID_HMAC_KEY is hidden, and the tests connect as the test role.

Every test runs with the key removed from the environment and with the
ingester settings told not to read the repository's .env, so a test can only
use the invented keys it passes in itself (Casey, 2026-11-13). The pipeline
settings read no .env either, from before the first test module is imported,
since pipeline/definitions.py builds its settings at import; every value the
tests' settings hold comes from the environment, which make exports from .env.

Every warehouse connection the tests open, the dbt runs they start included,
is made as the test role, which cannot write `raw`, `raw_dev_wh` or
`raw_dev_ta` (Casey, issue #55 comment 6961809084, item 10;
`docker/warehouse/test-role.sql`). The role's name and password are read once,
before any test module is imported, and held only in `tests/raw_schema.py`,
which `connect_or_skip` connects with. The environment gets placeholders in
WAREHOUSE_USER, WAREHOUSE_PASSWORD and the role's own two variables for the
whole session, so settings built later hold no test role value, and a test
that copies `os.environ` holds none either (Casey, issue #55 comment
7017280290, item 2). Only the modules in ROLE_IN_ENVIRONMENT, whose code
under test connects with credentials it reads from the environment, get the
values there, while they run.

With no test role configured, `connect_or_skip` logs in as a role that does
not exist, with a placeholder password, so no test connects as the owner:
where a warehouse answers, it refuses that login and each warehouse test
fails, saying what to set.

Neither value reaches a test or collection report, or a recorded warning:
both are replaced at any verbosity (`tests/report_redaction.py`). Output
written straight to the terminal, with `-s` or at a `--pdb` prompt, is not a
report, so it is not scrubbed.
"""

from collections.abc import Generator, Iterator
from typing import Any

import pytest
import raw_schema
import report_redaction

from ingester.settings import IngesterSettings, get_ingester_settings
from pipeline.common.settings import Settings, get_settings

# The test modules whose code under test connects with WAREHOUSE_USER and
# WAREHOUSE_PASSWORD from the environment: dbt runs in the first two, and the
# terrain registry's lookup opens its own connection from the settings in the
# third. A new module like these fails its warehouse tests with a refused
# login until it is added here.
ROLE_IN_ENVIRONMENT = frozenset(
    {"test_dbt_fixture_build", "test_dbt_targets", "test_terrain_snapshots_registry"}
)


def pytest_configure(config: pytest.Config) -> None:
    """Hold the test role privately and put placeholders in the environment for the session."""
    patch = pytest.MonkeyPatch()
    config.add_cleanup(patch.undo)
    config.add_cleanup(get_settings.cache_clear)
    config.add_cleanup(report_redaction.remember)
    config.add_cleanup(raw_schema.use_role)
    raw_schema.use_role(raw_schema.role_for_tests())
    report_redaction.remember(*(raw_schema._role[0] if raw_schema._role else ()))
    raw_schema.placeholder_environment(patch)
    # After the role is read: the main checkout's .env is where it comes from.
    patch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()


@pytest.fixture(autouse=True, scope="module")
def role_in_environment(request: pytest.FixtureRequest) -> Iterator[None]:
    """Give the modules in ROLE_IN_ENVIRONMENT the test role in the environment, while they run."""
    if request.module.__name__ not in ROLE_IN_ENVIRONMENT:
        yield
        return
    with pytest.MonkeyPatch.context() as patch:
        raw_schema.role_environment(patch)
        get_settings.cache_clear()
        yield
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


# A historic hook cannot be a wrapper; tryfirst runs this before the terminal
# reporter's own implementation stores the warning for its summary.
@pytest.hookimpl(tryfirst=True)
def pytest_warning_recorded(warning_message: Any) -> None:
    """Replace the test role's values in a warning before the warnings summary stores it."""
    report_redaction.scrub_warning(warning_message)


@pytest.fixture(autouse=True)
def no_real_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Hide the key from the environment and from the settings' .env file."""
    monkeypatch.delenv("NODE_ID_HMAC_KEY", raising=False)
    monkeypatch.setitem(IngesterSettings.model_config, "env_file", None)
    get_ingester_settings.cache_clear()
    yield
    get_ingester_settings.cache_clear()
