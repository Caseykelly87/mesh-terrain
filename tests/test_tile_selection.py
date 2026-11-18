"""Tile membership, the one-box placement, the busiest-pair rule, the DEM extent rule, drops.

Every node is invented. A node's boxes here stand for the boxes its pairs'
counted receipts used (`ingester.link_pairs`); a node with one box is placed
there, and a node with two is not placed unless it straddles the tile edge.

No test reads the real snapshot.
"""

from pathlib import Path

import pytest
from engine_inputs_fixture import (
    DEM_BOUNDS_M,
    G3_EDGE_SHIFT_M,
    NODES,
    STRADDLE_SHIFTS_M,
    WIDE_BITS,
    FakeNode,
    box_centre_m,
)

from engine.config import SnapshotIds
from engine.errors import ConfigError
from engine.grid import run_grid
from engine.points import Endpoint
from engine.snapshots import resolve_snapshots
from engine.terrain_build import check_covers
from ingester.link_cells import Cell, box_around, cell_of
from ingester.link_pairs import (
    USABLE_BITS,
    CaptureLinks,
    NodePositions,
    PairActivity,
    SelectionError,
)
from ingester.tile_selection import (
    BOTH_ENDS_PAIR,
    FAILS_DEM,
    GATEWAY_ONLY_PAIR,
    KIND_NOT_DETERMINED,
    OTHER_PAIR,
    STRADDLES,
    DroppedPair,
    Selection,
    busiest_two,
    dem_extent,
    dropped_pairs,
    inside_dem,
    pair_states,
    parse_tile,
    select_nodes,
    straddle_drops,
    tile_counts,
)
from terrain.draws import EndpointBox

FIXTURE_SNAPSHOTS = Path(__file__).resolve().parent / "fixtures" / "snapshots"
SUBTILE_DEM = "dem-1m-synthetic-subtile"
TILE = parse_tile("x72y429")


def pseudonym(name: str) -> str:
    return NODES[name].pseudonym


def pair(relayer: str, gateway: str, link_hours: int) -> PairActivity:
    return PairActivity(
        pseudonym(relayer), pseudonym(gateway), link_hours, link_hours, 0, 3 * link_hours
    )


def links(*pairs: PairActivity, nodes: dict[str, NodePositions] | None = None) -> CaptureLinks:
    """The pairs with every usable fixture node at its one box, unless nodes says otherwise."""
    found = {
        node.pseudonym: NodePositions(frozenset({cell_of(*node.box())}), False)
        for node in NODES.values()
        if node.precision_bits >= USABLE_BITS
    }
    found.update(nodes or {})
    return CaptureLinks(tuple(pairs), found, 0, 0)


def activity(name: str, hours: int, public: int, receipts: int) -> PairActivity:
    return PairActivity(f"r-{name}", f"g-{name}", hours, public, 0, receipts)


def two_boxes(name: str, east_m: float, north_m: float) -> dict[str, NodePositions]:
    """The node at its own box and at a second box shifted by the given metres."""
    node = NODES[name]
    boxes = frozenset({cell_of(*node.box()), cell_of(*node.box(east_m, north_m))})
    return {node.pseudonym: NodePositions(boxes, False)}


def straddling(name: str) -> dict[str, NodePositions]:
    """The node with a second box across the tile edge, as the capture fixture's second place."""
    return two_boxes(name, *STRADDLE_SHIFTS_M[name])


def nested(name: str) -> dict[str, NodePositions]:
    """The node at its own box and at the 15-bit box that holds it."""
    node = NODES[name]
    boxes = frozenset({cell_of(*node.box()), cell_of(*node.box(bits=WIDE_BITS))})
    return {node.pseudonym: NodePositions(boxes, False)}


def g3_at_edge() -> dict[str, NodePositions]:
    """G3 alone at a box whose run grid reaches past the DEM's east edge."""
    box = cell_of(*NODES["G3"].box(east_shift_m=G3_EDGE_SHIFT_M))
    return {pseudonym("G3"): NodePositions(frozenset({box}), False)}


def select(found: CaptureLinks, expect_pairs: int) -> Selection:
    return select_nodes(found, TILE, expect_pairs=expect_pairs, extent=DEM_BOUNDS_M)


# The fixture's three selected pairs: A to G1 with both ends in the tile, and
# B to G2 and C to G3 with only their gateway in it.
SELECTED = (pair("A", "G1", 2), pair("B", "G2", 5), pair("C", "G3", 4))
KINDS = {
    SELECTED[0]: BOTH_ENDS_PAIR,
    SELECTED[1]: GATEWAY_ONLY_PAIR,
    SELECTED[2]: GATEWAY_ONLY_PAIR,
}


def site_roles(selection: Selection) -> list[tuple[str, str]]:
    sites, _ = selection.layers()
    return [(site.pseudonym, site.role) for site in sites]


def touching_sites(found: CaptureLinks) -> list[tuple[str, str]]:
    """G1 as the both-ends gateway, then G2 and G3 in the capture's pair order."""
    gateway_only = {pseudonym("G2"), pseudonym("G3")}
    return [
        (pseudonym("G1"), "both_ends_gateway"),
        *((p.gateway, "gateway_only_gateway") for p in found.pairs if p.gateway in gateway_only),
    ]


def test_tile_bounds_follow_the_usgs_naming() -> None:
    edges = (TILE.west_m, TILE.south_m, TILE.east_m, TILE.north_m)
    assert edges == (720_000.0, 4_280_000.0, 730_000.0, 4_290_000.0)
    assert TILE.contains(720_000.0, 4_289_999.0)
    assert not TILE.contains(730_000.0, 4_285_000.0)


@pytest.mark.parametrize("name", ["x72", "X72Y429", "x72y429 ", "y429x72"])
def test_tile_refuses_other_names(name: str) -> None:
    with pytest.raises(SelectionError):
        parse_tile(name)


def test_busiest_two_ranks_by_link_hours() -> None:
    pairs = [activity("a", 2, 2, 6), activity("b", 5, 6, 18), activity("c", 4, 4, 12)]
    assert [found.relayer for found in busiest_two(pairs)] == ["r-b", "r-c"]


def test_receipts_break_a_tie_on_link_hours_for_second_place() -> None:
    pairs = [activity("a", 4, 4, 10), activity("b", 5, 6, 18), activity("c", 4, 4, 12)]
    assert [found.relayer for found in busiest_two(pairs)] == ["r-b", "r-c"]


def test_receipts_order_a_tie_on_link_hours_for_first_place() -> None:
    pairs = [activity("a", 5, 5, 15), activity("b", 5, 6, 18), activity("c", 4, 4, 12)]
    assert [found.relayer for found in busiest_two(pairs)] == ["r-b", "r-a"]


def test_a_tie_for_first_on_both_keys_keeps_the_input_order() -> None:
    pairs = [activity("a", 5, 5, 18), activity("b", 5, 6, 18), activity("c", 4, 4, 12)]
    assert [found.relayer for found in busiest_two(pairs)] == ["r-a", "r-b"]


@pytest.mark.parametrize(
    "other",
    [
        # Link-hours with a public receipt would rank a first.
        activity("a", 3, 7, 9),
        # Receipts alone would rank a first.
        activity("a", 3, 3, 30),
    ],
)
def test_other_measures_no_longer_refuse_or_change_the_pick(other: PairActivity) -> None:
    pairs = [other, activity("b", 5, 6, 18), activity("c", 4, 4, 12)]
    assert [found.relayer for found in busiest_two(pairs)] == ["r-b", "r-c"]


def test_busiest_two_refuses_a_tie_for_second_on_both_keys() -> None:
    pairs = [activity("a", 4, 4, 12), activity("b", 5, 6, 18), activity("c", 4, 4, 12)]
    with pytest.raises(SelectionError, match="tie for second busiest pair on link-hours"):
        busiest_two(pairs)


def test_busiest_two_refuses_fewer_than_two_pairs() -> None:
    with pytest.raises(SelectionError, match="1 pairs; the ranking needs two pairs"):
        busiest_two([activity("a", 4, 4, 12)])


def test_a_gateway_in_two_selected_pairs_is_one_site_with_the_both_ends_role() -> None:
    found = links(pair("A", "G1", 2), pair("B", "G1", 5), pair("C", "G3", 4), pair("D", "E", 1))
    sites, receiver = select(found, 4).layers()
    assert [(site.pseudonym, site.role) for site in sites] == [
        (pseudonym("G1"), "both_ends_gateway"),
        (pseudonym("G3"), "gateway_only_gateway"),
    ]
    assert receiver.pseudonym == pseudonym("A")


def test_two_gateway_only_pairs_sharing_a_gateway_give_one_site() -> None:
    found = links(pair("A", "G1", 2), pair("B", "G2", 5), pair("C", "G2", 4))
    assert site_roles(select(found, 3)) == [
        (pseudonym("G1"), "both_ends_gateway"),
        (pseudonym("G2"), "gateway_only_gateway"),
    ]


def test_sites_are_the_touching_gateways_when_the_busiest_pair_is_outside_the_tile() -> None:
    """D to E, outside the tile, is the busiest pair by link-hours and selects nothing."""
    found = links(*SELECTED, pair("D", "E", 9))
    assert busiest_two(found.pairs)[0] == pair("D", "E", 9)
    selection = select(found, 4)
    assert site_roles(selection) == touching_sites(found)
    assert selection.layers()[1].pseudonym == pseudonym("A")


def test_sites_follow_the_pair_order_not_the_busiest_ranking() -> None:
    """Swapping which gateway-only pair is busier leaves the sites and their order as they were."""
    one = links(pair("A", "G1", 2), pair("B", "G2", 5), pair("C", "G3", 4))
    other = links(pair("A", "G1", 2), pair("B", "G2", 4), pair("C", "G3", 5))
    assert site_roles(select(one, 3)) == site_roles(select(other, 3)) == touching_sites(one)


def test_a_busiest_both_ends_pair_changes_nothing() -> None:
    found = links(pair("A", "G1", 6), pair("B", "G2", 5), pair("C", "G3", 1))
    assert site_roles(select(found, 3)) == touching_sites(found)


def test_a_tie_for_second_busiest_does_not_refuse_the_selection() -> None:
    found = links(pair("A", "G1", 2), pair("B", "G2", 4), pair("C", "G3", 4), pair("D", "E", 6))
    with pytest.raises(SelectionError, match="tie for second busiest"):
        busiest_two(found.pairs)
    assert site_roles(select(found, 4)) == touching_sites(found)


@pytest.mark.parametrize(
    ("pairs", "gateway_only"),
    [
        ((pair("A", "G1", 2),), 0),
        ((pair("A", "G1", 2), pair("B", "G2", 5), pair("D", "E", 1)), 1),
        # Three touching pairs, one of them with only its relayer in the tile.
        ((pair("A", "G1", 2), pair("B", "G2", 5), pair("A", "E", 4)), 1),
    ],
)
def test_refuses_a_tile_without_two_gateway_only_pairs(
    pairs: tuple[PairActivity, ...], gateway_only: int
) -> None:
    message = f"{gateway_only} pairs have only the gateway end in x72y429, not two"
    with pytest.raises(SelectionError, match=message):
        select(links(*pairs), len(pairs))


def test_refuses_a_pair_node_without_a_box() -> None:
    found = links(pair("A", "G1", 1), pair("F", "G2", 5), pair("C", "G3", 4))
    with pytest.raises(SelectionError, match="a node of a pair has no box"):
        select(found, 3)


def test_a_straddling_node_counts_as_in_the_tile() -> None:
    states = pair_states(links(*SELECTED, pair("D", "E", 1), nodes=straddling("D")), TILE)
    assert states[pair("D", "E", 1)] == ("split", "out")
    assert tile_counts(states) == {
        "both ends": 1,
        "only the gateway end": 2,
        "only the relayer end": 1,
        "touched": 4,
    }


def test_a_selection_with_no_drop_gives_the_layers() -> None:
    selection = select(links(*SELECTED, pair("D", "E", 1)), 4)
    assert selection.dropped == ()
    assert all(selection.dem_check.values())
    assert site_roles(selection) == touching_sites(links(*SELECTED))
    assert selection.layers()[1].pseudonym == pseudonym("A")


def test_a_straddling_gateway_only_gateway_is_counted_then_its_pair_is_dropped() -> None:
    """G2 used two boxes, one on each side of the edge, so it is not placed and straddles."""
    assert straddling("G2")[pseudonym("G2")].one_box is None
    selection = select(links(*SELECTED, nodes=straddling("G2")), 3)
    assert selection.dropped == (
        DroppedPair(pair("B", "G2", 5), GATEWAY_ONLY_PAIR, (("gateway", STRADDLES),)),
    )
    assert pseudonym("G2") not in {node.pseudonym for node in selection.placed}


def test_a_straddling_both_ends_relayer_is_counted_then_its_pair_is_dropped() -> None:
    selection = select(links(*SELECTED, nodes=straddling("A")), 3)
    assert selection.dropped == (
        DroppedPair(pair("A", "G1", 2), BOTH_ENDS_PAIR, (("relayer", STRADDLES),)),
    )


def test_a_selected_gateway_failing_the_dem_check_drops_its_pair() -> None:
    selection = select(links(*SELECTED, nodes=g3_at_edge()), 3)
    assert selection.dem_check[pseudonym("G3")] is False
    assert selection.dropped == (
        DroppedPair(pair("C", "G3", 4), GATEWAY_ONLY_PAIR, (("gateway", FAILS_DEM),)),
    )


@pytest.mark.parametrize("nodes", [straddling("G2"), straddling("A"), g3_at_edge()])
def test_a_dropped_selected_pair_stops_the_layers_without_naming_a_node(
    nodes: dict[str, NodePositions],
) -> None:
    selection = select(links(*SELECTED, nodes=nodes), 3)
    with pytest.raises(SelectionError) as refused:
        selection.layers()
    assert "selected pairs dropped" in str(refused.value)
    assert "engine_inputs Q1 (a)" in str(refused.value)
    assert not any(node.pseudonym in str(refused.value) for node in NODES.values())


def test_an_unselected_pair_with_a_straddling_node_is_dropped_alone() -> None:
    states = pair_states(links(*SELECTED, pair("D", "E", 1), nodes=straddling("D")), TILE)
    dem_check = {pseudonym(name): True for name in ("G1", "G2", "G3", "A")}
    assert dropped_pairs(states, KINDS, dem_check) == (
        DroppedPair(pair("D", "E", 1), OTHER_PAIR, (("relayer", STRADDLES),)),
    )


def test_a_node_shared_with_a_dropped_pair_stays_in_the_kept_pair() -> None:
    kept, dropped = pair("A", "G1", 2), pair("D", "G1", 1)
    states = pair_states(links(kept, dropped, nodes=straddling("D")), TILE)
    found = dropped_pairs(states, {kept: BOTH_ENDS_PAIR}, {pseudonym("G1"): True})
    assert found == (DroppedPair(dropped, OTHER_PAIR, (("relayer", STRADDLES),)),)


def test_a_placed_node_failing_the_dem_check_drops_every_pair_it_is_in() -> None:
    selected, other = pair("C", "G3", 4), pair("D", "G3", 1)
    states = pair_states(links(selected, other, nodes=g3_at_edge()), TILE)
    found = dropped_pairs(states, {selected: GATEWAY_ONLY_PAIR}, {pseudonym("G3"): False})
    assert found == (
        DroppedPair(selected, GATEWAY_ONLY_PAIR, (("gateway", FAILS_DEM),)),
        DroppedPair(other, OTHER_PAIR, (("gateway", FAILS_DEM),)),
    )


def test_straddle_drops_name_the_kind_when_the_tile_check_holds() -> None:
    assert straddle_drops(links(*SELECTED, nodes=straddling("G2")), TILE, 3) == (
        DroppedPair(pair("B", "G2", 5), GATEWAY_ONLY_PAIR, (("gateway", STRADDLES),)),
    )


@pytest.mark.parametrize(
    ("name", "dropped", "end", "expect_pairs"),
    [
        # A fourth touching pair.
        ("D", pair("D", "E", 1), "relayer", 4),
        # Two pairs with both ends in the tile.
        ("B", SELECTED[1], "relayer", 4),
        # The checks would pass but for the pair count.
        ("G2", SELECTED[1], "gateway", 8),
    ],
)
def test_straddle_drops_leave_the_kind_open_when_the_tile_check_would_refuse(
    name: str, dropped: PairActivity, end: str, expect_pairs: int
) -> None:
    found = links(*SELECTED, pair("D", "E", 1), nodes=straddling(name))
    assert straddle_drops(found, TILE, expect_pairs) == (
        DroppedPair(dropped, KIND_NOT_DETERMINED, ((end, STRADDLES),)),
    )


def test_straddle_drops_leave_the_dem_edge_check_to_the_selection() -> None:
    assert straddle_drops(links(*SELECTED, nodes=g3_at_edge()), TILE, 3) == ()


def test_a_pair_failing_at_both_ends_names_both() -> None:
    both = pair("A", "G3", 2)
    states = pair_states(links(both, nodes={**straddling("A"), **g3_at_edge()}), TILE)
    found = dropped_pairs(states, {both: BOTH_ENDS_PAIR}, {pseudonym("G3"): False})
    assert found == (
        DroppedPair(both, BOTH_ENDS_PAIR, (("relayer", STRADDLES), ("gateway", FAILS_DEM))),
    )


def test_refuses_a_tile_without_exactly_one_both_ends_pair() -> None:
    found = links(pair("B", "G2", 5), pair("C", "G3", 4), pair("D", "E", 1))
    with pytest.raises(SelectionError, match="0 pairs have both ends in x72y429"):
        select(found, 3)


def test_refuses_other_than_three_pairs_touching_the_tile() -> None:
    found = links(pair("A", "G1", 1), pair("B", "G2", 5), pair("C", "G3", 4), pair("A", "E", 2))
    with pytest.raises(SelectionError, match="pairs touching x72y429 are not 3"):
        select(found, 4)


def test_an_unselected_pair_with_a_straddling_node_fails_the_touching_count() -> None:
    """Counted as in the tile, the straddling node makes a fourth touching pair."""
    found = links(*SELECTED, pair("D", "E", 1), nodes=straddling("D"))
    with pytest.raises(SelectionError, match="pairs touching x72y429 are not 3"):
        select(found, 4)


def test_a_straddling_gateway_only_relayer_fails_the_one_both_ends_pair_check() -> None:
    """Counted as in the tile, the relayer gives a gateway-only pair both ends in it."""
    found = links(*SELECTED, nodes=two_boxes("B", -10_000.0, 0.0))
    with pytest.raises(SelectionError, match="2 pairs have both ends in x72y429, not one"):
        select(found, 3)


def test_a_node_with_two_boxes_on_both_sides_straddles_even_when_they_nest() -> None:
    """G3's 15-bit box is centred east of the edge, its 18-bit box inside: two boxes, split."""
    assert not TILE.contains(*box_centre_m(NODES["G3"].box(bits=WIDE_BITS)))
    found = links(*SELECTED, nodes=nested("G3"))
    assert pair_states(found, TILE)[SELECTED[2]] == ("out", "split")
    assert select(found, 3).dropped == (
        DroppedPair(SELECTED[2], GATEWAY_ONLY_PAIR, (("gateway", STRADDLES),)),
    )


def test_a_node_is_placed_at_its_one_box_with_the_inscribed_half_width() -> None:
    box = cell_of(*NODES["G1"].box())
    selection = select(links(*SELECTED), 3)
    sites, _ = selection.layers()
    assert sites[0].pseudonym == pseudonym("G1")
    assert (sites[0].easting_m, sites[0].northing_m) == pytest.approx(
        box_centre_m(NODES["G1"].box()), abs=1e-3
    )
    assert sites[0].half_width_m == box.smaller_half_width_m == box.half_width_ew_m


def test_a_merged_box_is_placed_at_its_midpoint() -> None:
    """A rectangle around merged fixes is placed at the midpoint of its edges (lnk_02)."""
    latitude_i, longitude_i, _ = NODES["G1"].box()
    fixes = frozenset({Cell(latitude_i, longitude_i, 32), Cell(latitude_i, longitude_i + 920, 32)})
    merged, _ = box_around(fixes)
    found = links(*SELECTED, nodes={pseudonym("G1"): NodePositions(frozenset({merged}), True)})
    g1 = select(found, 3).layers()[0][0]
    assert merged.merged
    midpoint = box_centre_m((latitude_i, longitude_i + 460, 32))
    assert (g1.easting_m, g1.northing_m) == pytest.approx(midpoint, abs=0.01)
    assert g1.half_width_m == merged.smaller_half_width_m
    assert g1.precise_source


def test_refuses_a_selected_node_that_sent_two_boxes_without_naming_it() -> None:
    moved = FakeNode("A", 0x21, 726_500, 4_286_500, 16)
    boxes = frozenset({cell_of(*moved.box()), cell_of(*moved.box(east_shift_m=2_000))})
    found = links(
        pair("A", "G1", 1),
        pair("B", "G2", 5),
        pair("C", "G3", 4),
        nodes={pseudonym("A"): NodePositions(boxes, False)},
    )
    with pytest.raises(SelectionError) as refused:
        select(found, 3)
    assert "the both_ends_relayer used 2 boxes across its pairs" in str(refused.value)
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
