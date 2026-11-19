"""The test role's two values never reach a pytest report, at any verbosity.

Casey, issue #55 comment 7017280290, item 2: the values never appear in a
transcript or log. `tests/conftest.py` puts them into the environment for
every test, and a failure report can show them in several ways: the
assertion explanation at `-vv`, the locals of each frame with `-l` or
`--showlocals` (a psycopg connection's repr names its user), a chained
exception, or captured output. So conftest replaces both values in every
test and collection report (`tests/report_redaction.py`).

The probe runs pytest in a subprocess with invented values, through the real
`tests/conftest.py` loaded as a plugin, on a failing test of its own and on
`tests/test_dbt_targets.py`'s real test that once passed `os.environ` into
an assert; no real value is read.
"""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

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

import psycopg

import test_dbt_targets as targets


def test_a_failing_assert_with_the_password_in_a_local():
    secret = os.environ["WAREHOUSE_PASSWORD"]
    user = os.environ["WAREHOUSE_USER"]
    assert (user, secret) == ("someone", "else")


def test_a_driver_error_naming_the_role():
    user = os.environ["WAREHOUSE_USER"]
    raise psycopg.OperationalError(f'FATAL:  password authentication failed for user "{user}"')


def test_the_dbt_target_test_that_once_passed_the_environment(monkeypatch):
    def nothing_refused(*arguments, **keywords):
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(targets, "dbt_result", nothing_refused)
    test = targets.test_dbt_target_in_the_environment_cannot_start_a_real_build
    wants = inspect.signature(test).parameters
    test(**({"monkeypatch": monkeypatch} if "monkeypatch" in wants else {}))
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
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "conftest",
            "-p",
            "no:cacheprovider",
            "--rootdir",
            str(tmp_path),
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


def test_neither_value_appears_in_a_report_at_the_highest_verbosity(tmp_path: Path) -> None:
    output = run_probe(tmp_path, "-vv", "-l", "-rA")
    leaked = [value for value in (INVENTED_USER, INVENTED_PASSWORD) if value in output]
    assert leaked == [], "a probe report shows a test role value"
    assert "3 failed" in output
    assert report_redaction.PLACEHOLDER in output


def test_neither_value_appears_with_long_tracebacks_and_showlocals(tmp_path: Path) -> None:
    output = run_probe(tmp_path, "--tb=long", "--showlocals", "-vv")
    assert INVENTED_USER not in output
    assert INVENTED_PASSWORD not in output


def report(longrepr: object, sections: list[tuple[str, str]]) -> SimpleNamespace:
    return SimpleNamespace(longrepr=longrepr, sections=sections)


def test_a_text_report_and_its_sections_lose_both_values() -> None:
    report_redaction.remember("role_a", "secret_b")
    try:
        scrubbed = report("x role_a y secret_b", [("Captured stdout", "secret_b and role_a")])
        report_redaction.scrub(scrubbed)
    finally:
        report_redaction.remember()
    placeholder = report_redaction.PLACEHOLDER
    assert scrubbed.longrepr == f"x {placeholder} y {placeholder}"
    assert scrubbed.sections == [("Captured stdout", f"{placeholder} and {placeholder}")]


def test_a_skip_report_keeps_its_shape() -> None:
    report_redaction.remember("role_a", "secret_b")
    try:
        scrubbed = report(("file.py", 3, "Skipped: role_a"), [])
        report_redaction.scrub(scrubbed)
    finally:
        report_redaction.remember()
    assert scrubbed.longrepr == ("file.py", 3, f"Skipped: {report_redaction.PLACEHOLDER}")


def test_a_longer_value_is_replaced_before_one_it_contains() -> None:
    report_redaction.remember("abc", "abcdef")
    try:
        text = report_redaction.redacted("abcdef abc")
    finally:
        report_redaction.remember()
    assert text == f"{report_redaction.PLACEHOLDER} {report_redaction.PLACEHOLDER}"


def test_nothing_is_replaced_when_no_role_is_configured() -> None:
    report_redaction.remember()
    assert report_redaction.redacted("anything at all") == "anything at all"
