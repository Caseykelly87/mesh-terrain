"""Replace the test role's two values in pytest reports, so no transcript shows them.

Casey, issue #55 comment 7017280290, item 2: the values never appear in a
transcript or log. A failing test's report can carry them in its assertion
explanation (`-vv`), in the locals of each frame (`-l`, `--showlocals`; a
psycopg connection, cursor or transaction's repr names its user), in a
chained exception, or in captured output. `tests/conftest.py` remembers the
two values once, before any test runs, and scrubs every test and collection
report with this module; the values live only in this module's list, never in
a test's locals.
"""

from typing import Any

PLACEHOLDER = "<test role value>"
_values: list[str] = []


def remember(*values: str) -> None:
    """Replace these values from now on; with none, replace nothing."""
    _values[:] = sorted((value for value in values if value), key=len, reverse=True)


def redacted(text: str) -> str:
    """The text with every remembered value replaced, the longest first."""
    for value in _values:
        text = text.replace(value, PLACEHOLDER)
    return text


def scrub(report: Any) -> None:
    """Replace the remembered values in a report's traceback text and captured sections.

    A traceback becomes its rendered text, as pytest would print it; a skip's
    (path, line, reason) keeps its shape.
    """
    if not _values:
        return
    longrepr = report.longrepr
    if isinstance(longrepr, tuple):
        report.longrepr = (*longrepr[:-1], redacted(str(longrepr[-1])))
    elif longrepr is not None:
        text = str(longrepr)
        if redacted(text) != text:
            report.longrepr = redacted(text)
    report.sections = [(title, redacted(content)) for title, content in report.sections]
