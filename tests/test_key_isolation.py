"""No test sees the real NODE_ID_HMAC_KEY (Casey, 2026-11-13).

Tests use invented keys only. The real key must reach no test, either through
the environment (the Makefile used to export .env into every recipe) or through
a settings object reading the repository's .env file. No settings field holds
the key in the pipeline settings, but the whole file is still not read there.
"""

import os

import pytest

from ingester.settings import IngesterSettings, get_ingester_settings
from pipeline.common.settings import Settings

# Read when this module is collected, before any fixture runs, as
# pipeline/definitions.py builds its settings when it is imported.
PIPELINE_ENV_FILE_AT_COLLECTION = Settings.model_config.get("env_file")
INGESTER_ENV_FILE_AT_COLLECTION = IngesterSettings.model_config.get("env_file")


def test_the_environment_holds_no_key() -> None:
    # A bare `not in os.environ` would print the whole environment, key included, on failure.
    present = "NODE_ID_HMAC_KEY" in os.environ
    assert present is False


def test_the_settings_read_no_key() -> None:
    assert get_ingester_settings().node_id_hmac_key is None


def test_the_isolation_fixture_runs_for_every_test(request: pytest.FixtureRequest) -> None:
    # The two tests above pass on a machine with no key at all; these fail there too
    # if tests/conftest.py stops hiding the key.
    assert "no_real_key" in request.fixturenames
    assert IngesterSettings.model_config.get("env_file") is None


def test_the_pipeline_settings_read_no_dotenv_while_tests_are_collected() -> None:
    assert PIPELINE_ENV_FILE_AT_COLLECTION is None


def test_the_pipeline_settings_read_no_dotenv_while_a_test_runs() -> None:
    assert Settings.model_config.get("env_file") is None


def test_the_ingester_settings_read_no_dotenv_while_tests_are_collected() -> None:
    assert INGESTER_ENV_FILE_AT_COLLECTION is None
