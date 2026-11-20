"""The engine config and profiles: every field explicit, every refusal before any write.

docs/INTERFACE.md fixes the config fields and says no default is hidden in
code, so a missing field is refused as firmly as a wrong one. Each refusal is
a ConfigError raised by `load_config`, before the engine creates a folder.
"""

from pathlib import Path
from typing import Any

import pytest
from engine_fixture import FIXTURE_CONFIG, fixture_fields, no_logger_caching, write_config

from engine.config import CONFIG_FIELDS, SNAPSHOT_ROLES, load_config
from engine.errors import ConfigError
from engine.profiles import PROFILES_DIR, load_profile


def refusal(tmp_path: Path, profiles_dir: Path = PROFILES_DIR, **changes: Any) -> str:
    """Load the fixture config with `changes` applied; return the refusal message."""
    fields = fixture_fields()
    fields.update(changes)
    config = write_config(tmp_path, fields)
    with pytest.raises(ConfigError) as caught:
        load_config(config, profiles_dir=profiles_dir)
    return str(caught.value)


def snapshots(**changes: Any) -> dict[str, Any]:
    ids: dict[str, Any] = dict(fixture_fields()["snapshots"])
    ids.update(changes)
    return ids


def test_the_committed_fixture_config_loads() -> None:
    config = load_config(FIXTURE_CONFIG)
    assert config.run_id == "fixture"
    assert config.models == ("fspl", "knife_edge", "hata", "p1812")
    assert config.tx_profile.name == "site_gateway"
    assert config.tx_profile.height_m == 10.0
    assert config.rx_profile.name == "mesh_node"
    assert config.tx_sites == FIXTURE_CONFIG.parent / "two_sites.parquet"
    assert config.snapshots.dem == "dem-1m-synthetic-subtile"
    assert config.output_dir == Path("/runs")
    assert len(config.config_sha256) == 64
    assert config.position_floor_m == 5.0


@pytest.mark.parametrize("floor_m", [0, 0.0, 5, 12.5])
def test_a_position_floor_of_0_or_more_is_accepted(tmp_path: Path, floor_m: float) -> None:
    # Casey, issue #55 comment 7042577618, item 2: 0 turns the floor off.
    config = write_config(tmp_path, {**fixture_fields(), "position_floor_m": floor_m})
    assert load_config(config).position_floor_m == float(floor_m)


def test_shipped_profiles_hold_the_interface_values() -> None:
    node, gateway = load_profile("mesh_node"), load_profile("site_gateway")
    assert (node.height_m, node.gain_dbi, node.transmit_power_dbm) == (2.0, 0.0, 22.0)
    assert (gateway.height_m, gateway.gain_dbi, gateway.transmit_power_dbm) == (10.0, 3.0, 22.0)
    assert node.polarisation == gateway.polarisation == "vertical"
    assert node.description and gateway.description


@pytest.mark.parametrize("field", CONFIG_FIELDS)
def test_every_field_is_required(tmp_path: Path, field: str) -> None:
    fields = fixture_fields()
    del fields[field]
    config = write_config(tmp_path, {"output_dir": str(tmp_path / "out"), **fields})
    if field == "output_dir":
        config.write_text(config.read_text().replace(f"output_dir: {tmp_path / 'out'}\n", ""))
    with pytest.raises(ConfigError, match=field):
        load_config(config)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"colour": "blue"}, "unknown field colour"),
        ({"run_id": "../escape"}, "run_id"),
        ({"run_id": "Upper"}, "run_id"),
        ({"run_id": ""}, "run_id"),
        ({"frequency_mhz": 0}, "frequency_mhz"),
        ({"frequency_mhz": "906"}, "frequency_mhz"),
        ({"frequency_mhz": True}, "frequency_mhz"),
        ({"frequency_mhz": 100.0}, "hata is valid from 150 to 1500 MHz"),
        ({"models": []}, "models"),
        ({"models": "fspl"}, "models"),
        ({"models": ["fspl", "splat"]}, "splat"),
        ({"models": ["fspl", "fspl"]}, "twice"),
        ({"tx_profile": "tower"}, "no profile tower"),
        ({"rx_profile": "../mesh_node"}, "profile name"),
        ({"tx_sites": 3}, "tx_sites"),
        ({"rx_points": "missing.parquet"}, "rx_points"),
        ({"calibration": ["observations.parquet"]}, "calibration"),
        ({"endpoint_draws": 0}, "endpoint_draws"),
        ({"endpoint_draws": 2.5}, "endpoint_draws"),
        ({"endpoint_draws": True}, "endpoint_draws"),
        ({"position_floor_m": -1.0}, "position_floor_m"),
        ({"position_floor_m": float("nan")}, "position_floor_m"),
        ({"position_floor_m": float("inf")}, "position_floor_m"),
        ({"position_floor_m": "5"}, "position_floor_m"),
        ({"position_floor_m": True}, "position_floor_m"),
        ({"seed": -1}, "seed"),
        ({"seed": "20261108"}, "seed"),
        ({"output_dir": 7}, "output_dir"),
        ({"snapshots": ["dem-1m-synthetic-subtile"]}, "snapshots"),
        ({"snapshots": snapshots(dem=None)}, "snapshots.dem"),
        ({"snapshots": snapshots(dsm="Not An Id")}, "snapshots.dsm"),
        ({"snapshots": snapshots(lidar="x")}, "unknown field lidar"),
        ({"snapshots": snapshots(buildings=None)}, "hata needs snapshots.buildings"),
        ({"snapshots": snapshots(p1812_maps=None)}, "p1812 needs snapshots.p1812_maps"),
        ({"snapshots": snapshots(dsm=None, canopy=None)}, "p1812 needs snapshots.dsm or"),
    ],
)
def test_config_refusals(tmp_path: Path, changes: dict[str, Any], message: str) -> None:
    assert message in refusal(tmp_path, **changes)


@pytest.mark.parametrize("role", SNAPSHOT_ROLES)
def test_every_snapshot_role_is_required(tmp_path: Path, role: str) -> None:
    ids = snapshots()
    del ids[role]
    assert f"snapshots.{role}" in refusal(tmp_path, snapshots=ids)


def test_a_config_that_is_not_a_mapping_is_refused(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("- run_id\n- fixture\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_config(config)


def test_a_config_that_is_not_yaml_is_refused(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("run_id: [fixture\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML"):
        load_config(config)


def test_a_missing_config_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="no config file"):
        load_config(tmp_path / "absent.yaml")


def horizontal_profiles(tmp_path: Path) -> Path:
    folder = tmp_path / "profiles"
    folder.mkdir()
    for name in ("mesh_node", "site_gateway"):
        (folder / f"{name}.yaml").write_text((PROFILES_DIR / f"{name}.yaml").read_text())
    text = (PROFILES_DIR / "mesh_node.yaml").read_text().replace("vertical", "horizontal")
    (folder / "yagi_horizontal.yaml").write_text(text)
    return folder


@pytest.mark.parametrize("model", ["itm", "p1812"])
def test_mixed_polarisation_is_refused_for_itm_and_p1812(tmp_path: Path, model: str) -> None:
    message = refusal(
        tmp_path,
        horizontal_profiles(tmp_path),
        models=["fspl", model],
        rx_profile="yagi_horizontal",
    )
    assert "polarisation" in message


def test_mixed_polarisation_is_accepted_without_itm_or_p1812(tmp_path: Path) -> None:
    fields = fixture_fields()
    fields.update(models=["fspl", "knife_edge", "hata"], rx_profile="yagi_horizontal")
    config = load_config(write_config(tmp_path, fields), profiles_dir=horizontal_profiles(tmp_path))
    assert config.rx_profile.polarisation == "horizontal"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            "height_m: 2.0\ngain_dbi: 0.0\ntransmit_power_dbm: 22.0\npolarisation: vertical\n",
            "description",
        ),
        (
            "height_m: 2.0\ngain_dbi: 0.0\ntransmit_power_dbm: 22.0\npolarisation: vertical\n"
            "description: x\nfrequency_mhz: 915\n",
            "never carries a frequency",
        ),
        (
            "height_m: -2.0\ngain_dbi: 0.0\ntransmit_power_dbm: 22.0\npolarisation: vertical\n"
            "description: x\n",
            "height_m",
        ),
        (
            "height_m: 2.0\ngain_dbi: 0.0\ntransmit_power_dbm: 22.0\npolarisation: circular\n"
            "description: x\n",
            "polarisation",
        ),
        (
            "height_m: '2'\ngain_dbi: 0.0\ntransmit_power_dbm: 22.0\npolarisation: vertical\n"
            "description: x\n",
            "height_m",
        ),
    ],
)
def test_profile_refusals(tmp_path: Path, text: str, message: str) -> None:
    (tmp_path / "odd.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_profile("odd", tmp_path)


def test_a_profile_without_transmit_power_receives_only(tmp_path: Path) -> None:
    text = "height_m: 2.0\ngain_dbi: 0.0\ntransmit_power_dbm: null\npolarisation: vertical\n"
    (tmp_path / "listener.yaml").write_text(text + "description: receive only\n", encoding="utf-8")
    assert load_profile("listener", tmp_path).transmit_power_dbm is None


# The import above activates the autouse fixture for this module.
_ = no_logger_caching
