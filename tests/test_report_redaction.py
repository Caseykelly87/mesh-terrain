"""The test role's two values stay out of the environment and out of every pytest report.

Casey, issue #55 comment 7017280290, item 2: the values never appear in a
transcript or log. `tests/conftest.py` keeps them in `tests/raw_schema.py`'s
private holder and puts placeholders in the environment for the whole
session; only the modules in its ROLE_IN_ENVIRONMENT, whose code under test
connects with credentials from the environment (`test_dbt_fixture_build`,
`test_dbt_targets`, `test_terrain_snapshots_registry`), see the real values
there, while they run. So a test that copies `os.environ` elsewhere holds no
value.

A failure report could still show a value held elsewhere: the assertion
explanation at `-vv`, the locals of each frame with `-l` or `--showlocals` (a
psycopg connection's repr names its user), a chained exception, captured
output or a warning. conftest replaces both values in every test and
collection report and in every recorded warning (`tests/report_redaction.py`).

The probes run pytest in a subprocess with invented values, through the real
`tests/conftest.py` loaded as a plugin; no real value is read. Their first
tests are this module's own unit tests, so a unit test that left the shared
list empty would show in the failures after it.
"""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import raw_schema
import report_redaction

TESTS = Path(__file__).resolve().parent
INVENTED_USER = "invented_role_51ab"
INVENTED_PASSWORD = "invented_secret_8c3d"
NAMES = ("WAREHOUSE_TEST_USER", "WAREHOUSE_TEST_PASSWORD", "WAREHOUSE_USER", "WAREHOUSE_PASSWORD")
PASSED_DOWN = {"MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "PYTEST_ADDOPTS"}
PROBE = """
import inspect
import os
import subprocess
import warnings

import psycopg
import raw_schema

import test_dbt_targets as targets
from test_report_redaction import (
    test_a_text_report_and_its_sections_lose_both_values as test_a_unit_1,
    test_a_skip_report_keeps_its_shape as test_a_unit_2,
    test_a_longer_value_is_replaced_before_one_it_contains as test_a_unit_3,
    test_nothing_is_replaced_when_no_role_is_configured as test_a_unit_4,
)


def test_b_no_environment_variable_holds_a_value():
    assert not raw_schema.environment_holds_the_role()


def test_c_a_failing_assert_with_the_password_in_a_local():
    user, secret = raw_schema._role[0]
    assert (user, secret) == ("someone", "else")


def test_c_an_environment_copy_like_the_engine_tests_make():
    # Only the four names, padded past the 240 characters at which pytest
    # shortens a repr, so the probe's output holds no other variable.
    names = ("WAREHOUSE_USER", "WAREHOUSE_PASSWORD")
    names += ("WAREHOUSE_TEST_USER", "WAREHOUSE_TEST_PASSWORD")
    environment = {name: os.environ.get(name, "") for name in names}
    environment["padding"] = "x" * 300
    assert environment == {}


def test_d_a_driver_error_naming_the_role():
    user = raw_schema._role[0].user
    raise psycopg.OperationalError(f'FATAL:  password authentication failed for user "{user}"')


def test_e_the_dbt_target_test_that_once_passed_the_environment(monkeypatch):
    def nothing_refused(*arguments, **keywords):
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(targets, "dbt_result", nothing_refused)
    test = targets.test_dbt_target_in_the_environment_cannot_start_a_real_build
    wants = inspect.signature(test).parameters
    test(**({"monkeypatch": monkeypatch} if "monkeypatch" in wants else {}))


def test_f_warnings_naming_the_role():
    role = raw_schema._role[0]
    warnings.warn(UserWarning(f"connected as {role.user}"))
    warnings.warn(ResourceWarning(f"<psycopg.Connection [IDLE] (user={role.user}) at 0x0>"))
"""


def run_probe(tmp_path: Path, *options: str) -> str:
    """The probe's whole pytest output, run through tests/conftest.py with invented values."""
    probe = tmp_path / "test_probe.py"
    probe.write_text(PROBE, encoding="utf-8")
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in PASSED_DOWN and name not in NAMES
    }
    environment.update(
        {
            "WAREHOUSE_TEST_USER": INVENTED_USER,
            "WAREHOUSE_TEST_PASSWORD": INVENTED_PASSWORD,
            "WAREHOUSE_HOST": "127.0.0.1",
            "WAREHOUSE_PORT": "1",
            "PYTHONPATH": str(TESTS),
        }
    )
    command = [sys.executable, "-m", "pytest", "-p", "conftest", "-p", "no:cacheprovider"]
    result = subprocess.run(
        [
            *command,
            "--rootdir",
            str(tmp_path),
            "-W",
            "default::ResourceWarning",
            *options,
            str(probe),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        cwd=tmp_path,
    )
    return result.stdout + result.stderr


def assert_probe_ran_and_shows_no_value(output: str) -> None:
    leaked = [value for value in (INVENTED_USER, INVENTED_PASSWORD) if value in output]
    assert leaked == [], "a probe report shows a test role value"
    assert "4 failed" in output, "the probe did not run its failing tests"
    assert "6 passed" in output, "the probe's unit tests or environment test did not pass"
    assert report_redaction.PLACEHOLDER in output


def test_neither_value_appears_in_a_report_at_the_highest_verbosity(tmp_path: Path) -> None:
    output = run_probe(tmp_path, "-vv", "-l", "-rA")
    assert_probe_ran_and_shows_no_value(output)
    assert "connected as" in output, "the warnings summary did not run"


def test_neither_value_appears_with_long_tracebacks_and_showlocals(tmp_path: Path) -> None:
    assert_probe_ran_and_shows_no_value(run_probe(tmp_path, "--tb=long", "--showlocals", "-vv"))


def test_neither_value_appears_with_locals_at_default_verbosity(tmp_path: Path) -> None:
    """Default verbosity shortens a long repr by cutting out its middle."""
    assert_probe_ran_and_shows_no_value(run_probe(tmp_path, "-l", "-rf"))


def test_the_short_summary_keeps_each_failure_message(tmp_path: Path) -> None:
    output = run_probe(tmp_path, "-rf", "-vv")
    assert_probe_ran_and_shows_no_value(output)
    lines = output.splitlines()
    line = next(line for line in lines if line.startswith("FAILED") and "test_c_a_failing" in line)
    assert "AssertionError" in line, "the short summary lost the failure's message"


def test_a_one_line_traceback_names_the_crash_location(tmp_path: Path) -> None:
    output = run_probe(tmp_path, "--tb=line")
    assert_probe_ran_and_shows_no_value(output)
    assert "namespace(" not in output
    assert any(
        "test_probe.py:" in line and "AssertionError" in line for line in output.splitlines()
    )


def report(longrepr: object, sections: list[tuple[str, str]]) -> SimpleNamespace:
    return SimpleNamespace(longrepr=longrepr, sections=sections)


def test_a_text_report_and_its_sections_lose_both_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report_redaction, "_values", [])
    report_redaction.remember("role_a", "secret_b")
    scrubbed = report("x role_a y secret_b", [("Captured stdout", "secret_b and role_a")])
    report_redaction.scrub(scrubbed)
    placeholder = report_redaction.PLACEHOLDER
    assert str(scrubbed.longrepr) == f"x {placeholder} y {placeholder}"
    assert scrubbed.sections == [("Captured stdout", f"{placeholder} and {placeholder}")]


def test_a_skip_report_keeps_its_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report_redaction, "_values", [])
    report_redaction.remember("role_a", "secret_b")
    scrubbed = report(("file.py", 3, "Skipped: role_a"), [])
    report_redaction.scrub(scrubbed)
    assert scrubbed.longrepr == ("file.py", 3, f"Skipped: {report_redaction.PLACEHOLDER}")


def test_a_longer_value_is_replaced_before_one_it_contains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(report_redaction, "_values", [])
    report_redaction.remember("abc", "abcdef")
    text = report_redaction.redacted("abcdef abc")
    assert text == f"{report_redaction.PLACEHOLDER} {report_redaction.PLACEHOLDER}"


def test_nothing_is_replaced_when_no_role_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report_redaction, "_values", [])
    report_redaction.remember()
    assert report_redaction.redacted("anything at all") == "anything at all"


def test_a_warning_naming_a_value_is_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report_redaction, "_values", [])
    report_redaction.remember("role_a")
    record = SimpleNamespace(message=ResourceWarning("unclosed (user=role_a)"))
    report_redaction.scrub_warning(record)
    assert isinstance(record.message, ResourceWarning)
    assert str(record.message) == f"unclosed (user={report_redaction.PLACEHOLDER})"


def test_this_session_still_scrubs_after_the_unit_tests() -> None:
    """Runs after the unit tests above: they must leave conftest's list as it was."""
    assert raw_schema.redaction_covers_the_role()


def test_this_test_sees_no_test_role_value_in_the_environment() -> None:
    assert not raw_schema.environment_holds_the_role()
