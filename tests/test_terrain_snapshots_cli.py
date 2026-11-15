"""The command line: each subcommand reaches its builder with the parsed arguments."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from pipeline.terrain_snapshots import __main__ as cli


@pytest.fixture
def calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> list[tuple[str, tuple[Any, ...], dict[str, Any]]]:
    seen: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
    for name in ("build_dem", "build_osm", "build_itu_maps", "build_dsm", "build_canopy"):

        def fake(*args: Any, _name: str = name, **kwargs: Any) -> Path:
            seen.append((_name, args, kwargs))
            return tmp_path / "done"

        monkeypatch.setattr(cli, name, fake)
    # configure_logging caches loggers process-wide, which would hide later
    # tests' log lines from structlog's capture_logs.
    monkeypatch.setattr(cli, "configure_logging", lambda log_format: None)
    return seen


def test_each_subcommand_calls_its_builder(calls: list[Any], tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    for argv in (["dem"], ["itu-maps"], ["canopy"]):
        assert cli.main([*argv, "--root", str(root)]) == 0
    assert cli.main(["osm", "--date", "2026-11-01", "--root", str(root)]) == 0
    scratch = tmp_path / "scratch"
    args = ["dsm", "--osm-snapshot", "osm-geofabrik-mo-il-2026-11-01", "--scratch", str(scratch)]
    assert cli.main([*args, "--root", str(root)]) == 0
    assert [name for name, _, _ in calls] == [
        "build_dem",
        "build_itu_maps",
        "build_canopy",
        "build_osm",
        "build_dsm",
    ]
    assert calls[3][1] == (root, date(2026, 11, 1))
    assert calls[4][1] == (root, "osm-geofabrik-mo-il-2026-11-01", scratch)


def test_a_missing_argument_is_a_usage_error(calls: list[Any]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["dsm", "--root", "x"])
    assert exit_info.value.code == 2
    assert calls == []
