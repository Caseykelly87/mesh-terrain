"""Helpers for the engine tests: the committed fixture config, rewritten per test.

The committed `engine/fixtures/fixture-run.yaml` names its point layers
relative to itself and writes to `/runs`; a test copy points at the same
layers by absolute path and writes under the test's own folder. Snapshot ids
resolve under `tests/fixtures/snapshots`, the fixture run's SNAPSHOTS_DIR.
"""

from pathlib import Path
from typing import Any

import yaml
from engine.settings import EngineSettings

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "engine" / "fixtures"
FIXTURE_CONFIG = FIXTURES / "fixture-run.yaml"
SNAPSHOTS = REPO / "tests" / "fixtures" / "snapshots"
POINT_FIELDS = ("tx_sites", "rx_points", "calibration")


def fixture_fields() -> dict[str, Any]:
    """The committed fixture config as a mapping, its paths made absolute."""
    fields: dict[str, Any] = yaml.safe_load(FIXTURE_CONFIG.read_text(encoding="utf-8"))
    for name in POINT_FIELDS:
        if fields[name] is not None:
            fields[name] = str(FIXTURES / fields[name])
    return fields


def write_config(folder: Path, fields: dict[str, Any], name: str = "config.yaml") -> Path:
    """Write a config file holding exactly `fields`."""
    path = folder / name
    path.write_text(yaml.safe_dump(fields, sort_keys=False), encoding="utf-8")
    return path


def fixture_config(folder: Path, **overrides: Any) -> Path:
    """The fixture config with `overrides` applied, written into `folder`."""
    fields = fixture_fields()
    fields["output_dir"] = str(folder / "out")
    fields.update(overrides)
    return write_config(folder, fields)


def fixture_settings(**overrides: Any) -> EngineSettings:
    """Settings for the fixture run: the fixture snapshots, no ITM library."""
    values: dict[str, Any] = {
        "snapshots_dir": SNAPSHOTS,
        "itm_library_path": REPO / "no-itm-library-on-this-host.so",
        "itm_source_commit": None,
        "engine_git_commit": None,
    }
    values.update(overrides)
    return EngineSettings(**values)
