"""Antenna profiles: named YAML records under `engine/profiles/` (`docs/INTERFACE.md`).

A profile holds exactly five fields: antenna height above ground in metres,
gain in dBi, transmit power in dBm (null for a profile that only receives),
polarisation and a description. A frequency is refused: it is a run
parameter, never a profile field. Profiles are loaded with `yaml.safe_load`
only (Casey, PR #27). The models read only the height and the polarisation;
gain and power are carried so outputs can name the profile, never applied
(ADR 0006, addition A).
"""

import math
import re
from pathlib import Path
from typing import Any

import yaml

from engine.errors import ConfigError
from terrain.antenna import POLARISATIONS, AntennaProfile

PROFILES_DIR = Path(__file__).resolve().parent / "profiles"
PROFILE_FIELDS = ("height_m", "gain_dbi", "transmit_power_dbm", "polarisation", "description")
PROFILE_NAME = re.compile(r"[a-z0-9_]+")


def profile_path(name: str, profiles_dir: Path = PROFILES_DIR) -> Path:
    """The file of a named profile.

    Raises:
        ConfigError: if the name is not lowercase letters, digits and
            underscores, or no such file exists.
    """
    if not isinstance(name, str) or not PROFILE_NAME.fullmatch(name):
        raise ConfigError(f"profile name {name!r} must be lowercase letters, digits and _")
    path = profiles_dir / f"{name}.yaml"
    if not path.is_file():
        raise ConfigError(f"no profile {name} in {profiles_dir}")
    return path


def load_profile(name: str, profiles_dir: Path = PROFILES_DIR) -> AntennaProfile:
    """Read and check one named profile.

    Args:
        name: The profile's name, its file stem.
        profiles_dir: Folder of profile files; `engine/profiles` by default.

    Returns:
        The profile as the record every model reads.

    Raises:
        ConfigError: for a missing file, a field missing or extra, a
            frequency, or a value of the wrong type or range.
    """
    path = profile_path(name, profiles_dir)
    fields = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(fields, dict):
        raise ConfigError(f"profile {name} must be a mapping of fields")
    if "frequency_mhz" in fields:
        raise ConfigError(f"profile {name}: a profile never carries a frequency")
    _check_fields(name, fields)
    try:
        return AntennaProfile(
            name=name,
            height_m=_number(name, fields, "height_m"),
            gain_dbi=_number(name, fields, "gain_dbi"),
            transmit_power_dbm=_optional_number(name, fields, "transmit_power_dbm"),
            polarisation=fields["polarisation"],
            description=fields["description"],
        )
    except ValueError as err:
        raise ConfigError(f"profile {name}: {err}") from err


def _check_fields(name: str, fields: dict[str, Any]) -> None:
    missing = [field for field in PROFILE_FIELDS if field not in fields]
    extra = sorted(str(field) for field in fields if field not in PROFILE_FIELDS)
    if missing or extra:
        raise ConfigError(f"profile {name}: missing {missing}, unknown {extra}")
    if fields["polarisation"] not in POLARISATIONS:
        raise ConfigError(f"profile {name}: polarisation must be one of {POLARISATIONS}")
    if not isinstance(fields["description"], str) or not fields["description"].strip():
        raise ConfigError(f"profile {name}: description must be text")


def _number(name: str, fields: dict[str, Any], field: str) -> float:
    value = fields[field]
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ConfigError(f"profile {name}: {field} must be a finite number, got {value!r}")
    return float(value)


def _optional_number(name: str, fields: dict[str, Any], field: str) -> float | None:
    return None if fields[field] is None else _number(name, fields, field)
