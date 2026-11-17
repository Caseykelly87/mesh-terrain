"""The tile's nodes, the busiest-pair measures and the DEM extent rule, on invented nodes.

No test reads the real snapshot.
"""

from pathlib import Path

import pytest
from engine_inputs_fixture import NODES, FakeNode

from engine.config import SnapshotIds
from engine.errors import ConfigError
from engine.grid import run_grid
from engine.points import Endpoint
from engine.snapshots import resolve_snapshots
from engine.terrain_build import check_covers
from ingester.link_pairs import Box, CaptureLinks, NodePositions, PairActivity, SelectionError
from ingester.tile_selection import (
    busiest_two,
    dem_extent,
    inside_dem,
    parse_tile,
    select_nodes,
)
from terrain.draws import EndpointBox

FIXTURE_SNAPSHOTS = Path(__file__).resolve().parent / "fixtures" / "snapshots"
SUBTILE_DEM = "dem-1m-synthetic-subtile"
TILE = parse_tile("x72y429")


def pseudonym(name: str) -> str:
    return NODES[name].pseudonym


def pair(relayer: str, gateway: str, link_hours: int) -> PairActivity:
    return PairActivity(
        pseudonym(relayer), pseudonym(gateway), link_hours, 0, link_hours, 3 * link_hours
    )


def links(*pairs: PairActivity, nodes: dict[str, NodePositions] | None = None) -> CaptureLinks:
    """The pairs with every fixture node at its one box, unless nodes says otherwise."""
    found = {
        node.pseudonym: NodePositions(frozenset({Box(*node.box())}), False)
        for node in NODES.values()
    }
    found.update(nodes or {})
    return CaptureLinks(tuple(pairs), {}, found, 0, 0)


def activity(name: str, union: int, total: int, receipts: int) -> PairActivity:
    return PairActivity(f"r-{name}", f"g-{name}", total, 0, union, receipts)


def test_tile_bounds_follow_the_usgs_naming() -> None:
    edges = (TILE.west_m, TILE.south_m, TILE.east_m, TILE.north_m)
    assert edges == (720_000.0, 4_280_000.0, 730_000.0, 4_290_000.0)
    assert TILE.contains(720_000.0, 4_289_999.0)
    assert not TILE.contains(730_000.0, 4_285_000.0)


@pytest.mark.parametrize("name", ["x72", "X72Y429", "x72y429 ", "y429x72"])
def test_tile_refuses_other_names(name: str) -> None:
    with pytest.raises(SelectionError):
        parse_tile(name)


def test_busiest_two_when_every_measure_agrees() -> None:
    pairs = [activity("a", 2, 2, 6), activity("b", 5, 6, 18), activity("c", 4, 4, 12)]
    assert [found.relayer for found in busiest_two(pairs)] == ["r-b", "r-c"]


def test_busiest_two_refuses_when_measures_disagree() -> None:
    pairs = [activity("a", 3, 3, 30), activity("b", 5, 6, 18), activity("c", 4, 4, 12)]
    with pytest.raises(SelectionError, match="measure"):
        busiest_two(pairs)


def test_busiest_two_refuses_a_tie_for_second() -> None:
    pairs = [activity("a", 4, 4, 12), activity("b", 5, 6, 18), activity("c", 4, 4, 12)]
    with pytest.raises(SelectionError, match="tie"):
        busiest_two(pairs)


def test_a_gateway_in_two_selected_pairs_is_one_site_with_the_both_ends_role() -> None:
    found = links(pair("A", "G1", 2), pair("B", "G1", 5), pair("C", "G3", 4), pair("D", "E", 1))
    sites, receiver = select_nodes(found, TILE, expect_pairs=4)
    assert [(site.pseudonym, site.role) for site in sites] == [
        (pseudonym("G1"), "both_ends_gateway"),
        (pseudonym("G3"), "busy_pair_gateway"),
    ]
    assert receiver.pseudonym == pseudonym("A")


def test_refuses_fewer_than_two_pairs_to_rank() -> None:
    with pytest.raises(SelectionError, match="1 pairs; the selection needs two busy pairs"):
        select_nodes(links(pair("A", "G1", 2)), TILE, expect_pairs=1)


def test_refuses_a_pair_node_without_a_usable_position() -> None:
    found = links(pair("A", "G1", 1), pair("F", "G2", 5), pair("C", "G3", 4))
    with pytest.raises(SelectionError, match="no usable position"):
        select_nodes(found, TILE, expect_pairs=3)


def test_refuses_a_node_with_positions_on_both_sides_of_the_tile_edge() -> None:
    both_sides = frozenset({Box(*NODES["A"].box()), Box(*NODES["B"].box())})
    found = links(
        pair("A", "G1", 1),
        pair("B", "G2", 5),
        pair("C", "G3", 4),
        nodes={pseudonym("A"): NodePositions(both_sides, False)},
    )
    with pytest.raises(SelectionError, match="both sides of the tile edge"):
        select_nodes(found, TILE, expect_pairs=3)


def test_refuses_a_tile_without_exactly_one_both_ends_pair() -> None:
    found = links(pair("B", "G2", 5), pair("C", "G3", 4), pair("D", "E", 1))
    with pytest.raises(SelectionError, match="0 pairs have both ends in x72y429"):
        select_nodes(found, TILE, expect_pairs=3)


def test_refuses_busy_pairs_that_are_not_gateway_only_pairs_of_the_tile() -> None:
    found = links(pair("A", "G1", 6), pair("B", "G2", 5), pair("C", "G3", 1))
    with pytest.raises(SelectionError, match="not gateway-only pairs"):
        select_nodes(found, TILE, expect_pairs=3)


def test_refuses_other_than_three_pairs_touching_the_tile() -> None:
    found = links(pair("A", "G1", 1), pair("B", "G2", 5), pair("C", "G3", 4), pair("A", "E", 2))
    with pytest.raises(SelectionError, match="pairs touching x72y429 are not 3"):
        select_nodes(found, TILE, expect_pairs=4)


def test_refuses_a_selected_node_that_sent_two_boxes_without_naming_it() -> None:
    moved = FakeNode("A", 0x21, 726_500, 4_286_500, 16)
    boxes = frozenset({Box(*moved.box()), Box(*moved.box(east_shift_m=2_000))})
    found = links(
        pair("A", "G1", 1),
        pair("B", "G2", 5),
        pair("C", "G3", 4),
        nodes={pseudonym("A"): NodePositions(boxes, False)},
    )
    with pytest.raises(SelectionError) as refused:
        select_nodes(found, TILE, expect_pairs=3)
    assert "the both_ends_relayer sent 2 boxes" in str(refused.value)
    assert pseudonym("A") not in str(refused.value)


def _engine_covers(dem_snapshot: object, centre_xy_m: tuple[float, float], half_m: float) -> bool:
    box = EndpointBox(centre_xy_m=centre_xy_m, half_width_m=half_m)
    grid = run_grid([Endpoint(endpoint_id="x", box=box)], resolution_m=1.0)
    try:
        check_covers(dem_snapshot, grid)  # type: ignore[arg-type]
    except ConfigError:
        return False
    return True


@pytest.mark.parametrize(
    ("easting_m", "expected"),
    [(725_150.0, True), (725_149.0, False), (725_850.0, True), (725_851.0, False)],
)
def test_extent_check_gives_the_engines_verdict(easting_m: float, expected: bool) -> None:
    ids = SnapshotIds(dem=SUBTILE_DEM, dsm=None, canopy=None, buildings=None, p1812_maps=None)
    dem = resolve_snapshots(ids, FIXTURE_SNAPSHOTS)["dem"]
    centre_xy_m, half_m = (easting_m, 4_285_500.0), 100.0
    extent = dem_extent(FIXTURE_SNAPSHOTS, SUBTILE_DEM)
    assert inside_dem(centre_xy_m, half_m, extent) is expected
    assert _engine_covers(dem, centre_xy_m, half_m) is expected


def test_dem_extent_refuses_a_missing_snapshot(tmp_path: Path) -> None:
    with pytest.raises(SelectionError, match="no finished folder"):
        dem_extent(tmp_path, "no-such-dem")
