"""No test sees the real NODE_ID_HMAC_KEY (Casey, 2026-11-13).

Tests use invented keys only. The real key must reach no test, either through
the environment (the Makefile used to export .env into every recipe) or through
the settings object reading the repository's .env file.
"""

import os

from ingester.settings import get_ingester_settings


def test_the_environment_holds_no_key() -> None:
    # A bare `not in os.environ` would print the whole environment, key included, on failure.
    present = "NODE_ID_HMAC_KEY" in os.environ
    assert present is False


def test_the_settings_read_no_key() -> None:
    assert get_ingester_settings().node_id_hmac_key is None
