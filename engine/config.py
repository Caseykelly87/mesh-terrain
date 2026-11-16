"""The run config: one YAML file, every field explicit, checked before anything is written.

`docs/INTERFACE.md` names the fields. They are all required, `calibration`
and the optional snapshot roles included (null where unused), because the
contract hides no default in code. Paths are relative to the config file's
folder. `snapshots` maps five roles to snapshot ids:

- `dem`: the DEM, always;
- `dsm`, `canopy`: the 1 m first-return surface and the canopy height map,
  either or both, null without them;
- `buildings`: the OpenStreetMap buildings snapshot, required by `hata`;
- `p1812_maps`: the ITU-R P.1812 map snapshot, required by `p1812` together
  with a DSM or canopy snapshot (ADR 0006 decisions 2 and 4).

The file is loaded with `yaml.safe_load` only (Casey, PR #27). Every refusal
is a ConfigError raised before the engine creates a folder.
"""

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from engine.errors import ConfigError
from engine.profiles import PROFILES_DIR, load_profile, profile_path
from terrain.antenna import AntennaProfile
from terrain.baselines import HATA_MAX_MHZ, HATA_MIN_MHZ
from terrain.path_loss import MODELS, Model

CONFIG_FIELDS = (
    "run_id",
    "frequency_mhz",
    "models",
    "tx_profile",
    "rx_profile",
    "tx_sites",
    "rx_points",
    "snapshots",
    "endpoint_draws",
    "seed",
    "calibration",
    "output_dir",
)
SNAPSHOT_ROLES = ("dem", "dsm", "canopy", "buildings", "p1812_maps")
RUN_ID = re.compile(r"[a-z0-9][a-z0-9-]*")
SNAPSHOT_ID = re.compile(r"[a-z0-9][a-z0-9-]*")


@dataclass(frozen=True, kw_only=True)
class SnapshotIds:
    """The snapshot id named for each role; None where the config names none."""

    dem: str
    dsm: str | None
    canopy: str | None
    buildings: str | None
    p1812_maps: str | None

    def named(self) -> dict[str, str]:
        """Role to id for every role the config names, in role order."""
        ids = {role: getattr(self, role) for role in SNAPSHOT_ROLES}
        return {role: snapshot_id for role, snapshot_id in ids.items() if snapshot_id}


@dataclass(frozen=True, kw_only=True)
class RunConfig:
    """A checked run config.

    Attributes:
        path: The config file.
        config_sha256: sha256 of the config file's bytes.
        run_id: Name of the run and of its output folder.
        frequency_mhz: Carrier frequency in MHz.
        models: The models to run, in config order.
        tx_profile: The transmitter profile, at every site.
        rx_profile: The receiver profile, at every receiver.
        profile_files: The two profile files, for the manifest's inputs.
        tx_sites: Site point layer (GeoParquet).
        rx_points: Receiver point layer (GeoParquet).
        calibration: Observations layer, or None.
        input_names: Field name to the path as the config wrote it.
        snapshots: Snapshot ids by role.
        endpoint_draws: Draws per pair when either end has a position box.
        seed: The run seed every draw derives from.
        output_dir: Folder that receives `<run_id>/`.
    """

    path: Path
    config_sha256: str
    run_id: str
    frequency_mhz: float
    models: tuple[Model, ...]
    tx_profile: AntennaProfile
    rx_profile: AntennaProfile
    profile_files: tuple[Path, Path]
    tx_sites: Path
    rx_points: Path
    calibration: Path | None
    input_names: dict[str, str]
    snapshots: SnapshotIds
    endpoint_draws: int
    seed: int
    output_dir: Path


def load_config(path: Path, *, profiles_dir: Path = PROFILES_DIR) -> RunConfig:
    """Read and check a run config.

    Args:
        path: The YAML config file.
        profiles_dir: Folder of profile files; `engine/profiles` by default.

    Returns:
        The checked config, paths resolved against the file's folder.

    Raises:
        ConfigError: for any field missing, unknown, of the wrong type or out
            of range, a profile or input file that does not exist, or a
            combination of models and snapshots the engine cannot run.
    """
    raw, fields = _read_fields(path)
    folder = path.resolve().parent
    models = _models(fields["models"])
    config = RunConfig(
        path=path,
        config_sha256=hashlib.sha256(raw).hexdigest(),
        run_id=_run_id(fields["run_id"]),
        frequency_mhz=_frequency(fields["frequency_mhz"]),
        models=models,
        tx_profile=load_profile(fields["tx_profile"], profiles_dir),
        rx_profile=load_profile(fields["rx_profile"], profiles_dir),
        profile_files=(
            profile_path(fields["tx_profile"], profiles_dir),
            profile_path(fields["rx_profile"], profiles_dir),
        ),
        tx_sites=_input_file(folder, fields, "tx_sites"),
        rx_points=_input_file(folder, fields, "rx_points"),
        calibration=_optional_input_file(folder, fields, "calibration"),
        input_names=_input_names(fields),
        snapshots=_snapshot_ids(fields["snapshots"]),
        endpoint_draws=_integer_at_least(fields["endpoint_draws"], "endpoint_draws", minimum=1),
        seed=_integer_at_least(fields["seed"], "seed", minimum=0),
        output_dir=_folder(folder, fields["output_dir"]),
    )
    _check_combination(config)
    return config


def _read_fields(path: Path) -> tuple[bytes, dict[str, Any]]:
    if not path.is_file():
        raise ConfigError(f"no config file at {path}")
    raw = path.read_bytes()
    try:
        fields = yaml.safe_load(raw)
    except yaml.YAMLError as err:
        raise ConfigError(f"{path} is not valid YAML: {err}") from err
    if not isinstance(fields, dict):
        raise ConfigError(f"{path} must hold a mapping of the config fields")
    _require_exact_fields(fields, CONFIG_FIELDS, "")
    return raw, fields


def _require_exact_fields(fields: dict[str, Any], expected: tuple[str, ...], prefix: str) -> None:
    missing = [f"{prefix}{name}" for name in expected if name not in fields]
    if missing:
        raise ConfigError(f"the config misses {', '.join(missing)}; every field is explicit")
    unknown = sorted(str(name) for name in fields if name not in expected)
    if unknown:
        raise ConfigError(f"unknown field {', '.join(unknown)}")


def _run_id(value: Any) -> str:
    if not isinstance(value, str) or not RUN_ID.fullmatch(value):
        raise ConfigError(f"run_id must be lowercase letters, digits and hyphens, got {value!r}")
    return value


def _frequency(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"frequency_mhz must be a positive number, got {value!r}")
    if not (math.isfinite(value) and value > 0.0):
        raise ConfigError(f"frequency_mhz must be a positive number, got {value!r}")
    return float(value)


def _models(value: Any) -> tuple[Model, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"models must be a non-empty list from {MODELS}, got {value!r}")
    for model in value:
        if model not in MODELS:
            raise ConfigError(f"unknown model {model!r}; models come from {MODELS}")
    if len(set(value)) != len(value):
        raise ConfigError(f"a model is listed twice in {value}")
    return tuple(cast(list[Model], value))


def _input_file(folder: Path, fields: dict[str, Any], name: str) -> Path:
    value = fields[name]
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{name} must be a path, got {value!r}")
    path = folder / value
    if not path.is_file():
        raise ConfigError(f"{name}: no file at {path}")
    return path


def _optional_input_file(folder: Path, fields: dict[str, Any], name: str) -> Path | None:
    return None if fields[name] is None else _input_file(folder, fields, name)


def _input_names(fields: dict[str, Any]) -> dict[str, str]:
    names = ("tx_sites", "rx_points", "calibration")
    return {name: fields[name] for name in names if fields[name] is not None}


def _snapshot_ids(value: Any) -> SnapshotIds:
    if not isinstance(value, dict):
        raise ConfigError(f"snapshots must map {SNAPSHOT_ROLES} to ids, got {value!r}")
    _require_exact_fields(value, SNAPSHOT_ROLES, "snapshots.")
    for role, snapshot_id in value.items():
        if snapshot_id is None and role != "dem":
            continue
        if not isinstance(snapshot_id, str) or not SNAPSHOT_ID.fullmatch(snapshot_id):
            raise ConfigError(f"snapshots.{role} must be a snapshot id, got {snapshot_id!r}")
    return SnapshotIds(**value)


def _integer_at_least(value: Any, name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{name} must be an integer of at least {minimum}, got {value!r}")
    return value


def _folder(folder: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"output_dir must be a path, got {value!r}")
    path = Path(value)
    # On Windows "/runs" has a root but no drive; it is the container path, kept as written.
    return path if path.anchor else folder / path


def _check_combination(config: RunConfig) -> None:
    """Refuse model and snapshot combinations that would fail or give an all-null column."""
    ids, models = config.snapshots, config.models
    if "hata" in models and ids.buildings is None:
        raise ConfigError("hata needs snapshots.buildings for its variant (ADR 0006 decision 7.1)")
    if "hata" in models and not HATA_MIN_MHZ <= config.frequency_mhz <= HATA_MAX_MHZ:
        raise ConfigError(f"hata is valid from {HATA_MIN_MHZ:g} to {HATA_MAX_MHZ:g} MHz")
    if "p1812" in models and ids.p1812_maps is None:
        raise ConfigError("p1812 needs snapshots.p1812_maps (ADR 0006 decision 2)")
    if "p1812" in models and ids.dsm is None and ids.canopy is None:
        raise ConfigError("p1812 needs snapshots.dsm or snapshots.canopy (ADR 0006 option 4.1)")
    polarised = {"itm", "p1812"} & set(models)
    if polarised and config.tx_profile.polarisation != config.rx_profile.polarisation:
        raise ConfigError(
            f"{' and '.join(sorted(polarised))} take one polarisation; the profiles differ "
            f"({config.tx_profile.name} {config.tx_profile.polarisation}, "
            f"{config.rx_profile.name} {config.rx_profile.polarisation})"
        )
