"""Every caller passes the GPS error floor explicitly (ADR 0009).

Casey, verbatim (2026-11-20): "In ingest-eng's tile_selection unit, remove the
two default floor arguments of 0 so every caller passes the floor explicitly,
with a test." These tests fail if `engine.grid.run_grid` or
`terrain.boxes.EdgeBox.extent_m` takes a default floor again. The box and the
point are made up.
"""

from typing import Any

import pytest

from engine.grid import run_grid
from engine.points import Endpoint
from terrain.boxes import EdgeBox
from terrain.draws import EndpointBox

CELL = EdgeBox(south_i=386000000, north_i=386000001, west_i=-903000000, east_i=-902999999)
POINT = EndpointBox(centre_xy_m=(725_000.0, 4_285_000.0), half_width_m=0.0)


def test_run_grid_has_no_default_floor() -> None:
    endpoints = [Endpoint(endpoint_id="a", box=POINT)]
    call: Any = run_grid
    with pytest.raises(TypeError, match="floor_m"):
        call(endpoints, 1.0)
    assert run_grid(endpoints, 1.0, floor_m=0.0).west_m == 724_950.0


@pytest.mark.parametrize("box", [CELL, POINT], ids=["edges", "square"])
def test_a_box_extent_has_no_default_floor(box: EdgeBox | EndpointBox) -> None:
    call: Any = box.extent_m
    with pytest.raises(TypeError, match="floor_m"):
        call()
    with pytest.raises(TypeError, match="floor_m"):
        call(50.0)
    assert box.extent_m(50.0, floor_m=0.0) == box.extent_m(margin_m=50.0, floor_m=0.0)
