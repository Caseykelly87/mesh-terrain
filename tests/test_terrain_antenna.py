"""Antenna profiles: the named records path_loss_db reads heights and polarisation from."""

import math
from typing import Any

import pytest

from terrain.antenna import AntennaProfile

MESH_NODE: dict[str, Any] = {
    "name": "mesh_node",
    "height_m": 2.0,
    "gain_dbi": 0.0,
    "polarisation": "vertical",
    "transmit_power_dbm": 22.0,
    "description": "handheld or rooftop node",
}


def test_mesh_node_profile_holds_the_interface_values() -> None:
    """docs/INTERFACE.md, Profiles: 2 m, 0 dBi, 22 dBm, vertical."""
    node = AntennaProfile(**MESH_NODE)
    assert (node.height_m, node.gain_dbi, node.transmit_power_dbm) == (2.0, 0.0, 22.0)
    assert node.polarisation == "vertical"


def test_a_receive_only_profile_has_no_transmit_power() -> None:
    fields = {key: value for key, value in MESH_NODE.items() if key != "transmit_power_dbm"}
    assert AntennaProfile(**fields).transmit_power_dbm is None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("height_m", 0.0, "height_m"),
        ("height_m", -2.0, "height_m"),
        ("height_m", math.nan, "height_m"),
        ("height_m", math.inf, "height_m"),
        ("gain_dbi", math.nan, "gain_dbi"),
        ("transmit_power_dbm", math.inf, "transmit_power_dbm"),
        ("polarisation", "circular", "polarisation"),
        ("name", "", "name"),
    ],
)
def test_malformed_profiles_are_refused(field: str, value: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        AntennaProfile(**{**MESH_NODE, field: value})
