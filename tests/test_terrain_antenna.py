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
