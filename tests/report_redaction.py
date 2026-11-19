"""Replace the test role's two values in pytest reports and warnings, so no transcript shows them.

Casey, issue #55 comment 7017280290, item 2: the values never appear in a
transcript or log. `tests/conftest.py` keeps the values out of the
environment of almost every test (`tests/raw_schema.py`), so most reports
never hold them; this module is the second guard. A failing test's report
can still carry a value in its assertion explanation (`-vv`), in the locals
of each frame (`-l`, `--showlocals`; a psycopg connection, cursor or
transaction's repr names its user), in a chained exception or in captured
output, and pytest's warnings summary can show a warning's text, such as an
unclosed connection's ResourceWarning, which holds the connection's repr.
conftest remembers the two values once and scrubs every test and collection
report and every recorded warning with this module; the values live only in
this module's list, never in a test's locals.

Every occurrence of a remembered value is replaced, whatever surrounds it, so
a short or common role name would also be replaced inside unrelated text,
such as a path or a column name, and those reports would be harder to read:
the README asks for a distinctive role name.
"""

from types import SimpleNamespace
from typing import Any

PLACEHOLDER = "<test role value>"
_values: list[str] = []


class ScrubbedText(str):
    """A rendered traceback with the values replaced, keeping the crash line `-rf` prints."""

    reprcrash: Any = None


def remember(*values: str) -> None:
    """Replace these values from now on; with none, replace nothing."""
    _values[:] = sorted((value for value in values if value), key=len, reverse=True)


def covers(*values: str) -> bool:
    """Whether every one of these values is being replaced."""
    return all(value in _values for value in values if value)


def redacted(text: str) -> str:
    """The text with every remembered value replaced, the longest first."""
    for value in _values:
        text = text.replace(value, PLACEHOLDER)
    return text


def _scrubbed_text(longrepr: Any) -> str:
    """The rendered traceback, scrubbed, with a scrubbed copy of its crash line when it has one."""
    text = ScrubbedText(redacted(str(longrepr)))
    crash = getattr(longrepr, "reprcrash", None)
    if crash is not None:
        text.reprcrash = SimpleNamespace(
            path=crash.path, lineno=crash.lineno, message=redacted(crash.message)
        )
    return text


def scrub(report: Any) -> None:
    """Replace the remembered values in a report's traceback text and captured sections.

    A traceback that holds a value becomes its rendered text, as pytest would
    print it, keeping its crash line for the short summary; a skip's (path,
    line, reason) keeps its shape.
    """
    if not _values:
        return
    longrepr = report.longrepr
    if isinstance(longrepr, tuple):
        report.longrepr = (*longrepr[:-1], redacted(str(longrepr[-1])))
    elif longrepr is not None and redacted(str(longrepr)) != str(longrepr):
        report.longrepr = _scrubbed_text(longrepr)
    report.sections = [(title, redacted(content)) for title, content in report.sections]


def scrub_warning(record: Any) -> None:
    """Replace the remembered values in a recorded warning's message, keeping its category."""
    if not _values:
        return
    message = record.message
    text = str(message)
    if redacted(text) == text:
        return
    try:
        record.message = type(message)(redacted(text))
    except TypeError:
        record.message = redacted(text)
