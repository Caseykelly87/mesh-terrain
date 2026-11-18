"""Tile membership, the one-box placement, the busiest-pair rule, the DEM extent rule, drops.

Every node is invented. A pair's boxes here stand for the boxes its counted
receipts in kept hours used (`ingester.link_pairs`); a node is placed once per
pair at the box that pair used, one layer row per distinct box, and each pair
is judged at the tile edge by its own rows (Casey, issue #55 comment
6985802330, items 3 and 6).

No test reads the real snapshot.
"""

from pathlib import Path

import pytest
from engine_inputs_fixture import (
    DEM_BOUNDS_M,
    G3_EDGE_SHIFT_M,
    NARROW_DEM_BOUNDS_M,
    NODES,
    STRADDLE_SHIFTS_M,
    FakeNode,
    box_centre_m,
)

from engine.config import SnapshotIds
from engine.errors import ConfigError
from engine.grid import run_grid
from engine.points import Endpoint
from engine.snapshots import resolve_snapshots
from engine.terrain_build import check_covers
from ingester import tile_selection
from ingester.link_cells import Cell, box_around, cell_of
from ingester.link_pairs import (
    USABLE_BITS,
    CaptureLinks,
    MovedPair,
    NodePositions,
    PairActivity,
    SelectionError,
)
from ingester.tile_selection import (
    BOTH_ENDS_PAIR,
    FAILS_DEM,
    GATEWAY_ONLY_PAIR,
    KIND_NOT_DETERMINED,
    MORE_THAN_ONE_BOX,
    OTHER_PAIR,
    STRADDLES,
    AnyPair,
    DroppedPair,
    Extent,
    KindConflict,
    PlacedNode,
    Selection,
    busiest_two,
    counted_states,
    dem_extent,
    dropped_pairs,
    inside_dem,
    kind_conflicts,
    pair_states,
    parse_tile,
    row_state,
    select_nodes,
    select_sites,
    site_row_ids,
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


def select(found: CaptureLinks, expect_pairs: int, extent: Extent = DEM_BOUNDS_M) -> Selection:
    return select_nodes(found, TILE, expect_pairs=expect_pairs, extent=extent)


def boxed(
    relayer: str,
    gateway: str,
    link_hours: int,
    relayer_shift: tuple[float, float] = (0.0, 0.0),
    gateway_shift: tuple[float, float] = (0.0, 0.0),
) -> PairActivity:
    """A pair carrying its boxes: each end's own box, or it shifted by (east, north) metres."""
    return PairActivity(
        pseudonym(relayer), pseudonym(gateway), link_hours, link_hours, 0, 3 * link_hours,
        cell_of(*NODES[relayer].box(*relayer_shift)), cell_of(*NODES[gateway].box(*gateway_shift)),
    )  # fmt: skip


# The fixture's three selected pairs: A to G1 with both ends in the tile, and
# B to G2 and C to G3 with only their gateway in it.
SELECTED = (pair("A", "G1", 2), pair("B", "G2", 5), pair("C", "G3", 4))
# The same pairs carrying their boxes, as link_pairs gives them; a node with a
# second box (a straddler) used it in a pair not listed here.
BOXED = (boxed("A", "G1", 2), boxed("B", "G2", 5), boxed("C", "G3", 4))


def across(relayer: str, gateway: str, link_hours: int, end: str) -> PairActivity:
    """The pair with that end's box moved across the tile edge (STRADDLE_SHIFTS_M)."""
    name = relayer if end == "relayer" else gateway
    shift = STRADDLE_SHIFTS_M[name]
    if end == "relayer":
        return boxed(relayer, gateway, link_hours, relayer_shift=shift)
    return boxed(relayer, gateway, link_hours, gateway_shift=shift)


KINDS: dict[AnyPair, str] = {
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


def test_a_row_is_in_out_or_across_the_tile_edge_by_its_box() -> None:
    """Casey, issue #55 comment 6985802330, item 6: each pair is judged by its own row."""
    assert row_state(cell_of(*NODES["G2"].box()), TILE) == "in"
    assert row_state(cell_of(*NODES["E"].box()), TILE) == "out"
    assert row_state(cell_of(*NODES["G2"].box(*STRADDLE_SHIFTS_M["G2"])), TILE) == "split"
    assert row_state(cell_of(*NODES["G3"].box(G3_EDGE_SHIFT_M)), TILE) == "split"


def test_a_straddling_row_counts_its_node_as_touching() -> None:
    d_across = across("D", "E", 1, "relayer")
    found = links(*BOXED, d_across)
    assert pair_states(found, TILE)[d_across] == ("split", "out")
    assert counted_states(found, TILE)[d_across] == ("in", "out")
    assert tile_counts(counted_states(found, TILE)) == {
        "both ends": 1,
        "only the gateway end": 2,
        "only the relayer end": 1,
        "touched": 4,
    }


def test_a_node_with_any_row_touching_counts_as_touching_in_every_pair() -> None:
    """G2's row with B is inside, its row with E's pair outside: both pairs count G2 in."""
    outside = boxed("D", "G2", 1, gateway_shift=(0.0, 20_000.0))
    found = links(*BOXED, outside)
    assert pair_states(found, TILE)[outside] == ("out", "out")
    assert counted_states(found, TILE)[outside] == ("out", "in")


def test_a_selection_with_no_drop_gives_the_layers() -> None:
    selection = select(links(*SELECTED, pair("D", "E", 1)), 4)
    assert selection.dropped == ()
    assert all(selection.dem_check.values())
    assert site_roles(selection) == touching_sites(links(*SELECTED))
    assert selection.layers()[1].pseudonym == pseudonym("A")


def test_a_gateway_only_pair_whose_gateway_row_straddles_is_counted_then_dropped() -> None:
    g2_across = across("B", "G2", 5, "gateway")
    selection = select(links(BOXED[0], g2_across, BOXED[2]), 3)
    assert selection.dropped == (
        DroppedPair(g2_across, GATEWAY_ONLY_PAIR, (("gateway", STRADDLES),)),
    )
    assert pseudonym("G2") not in {node.pseudonym for node in selection.placed}


def test_a_both_ends_pair_whose_relayer_row_straddles_is_counted_then_dropped() -> None:
    a_across = across("A", "G1", 2, "relayer")
    selection = select(links(a_across, BOXED[1], BOXED[2]), 3)
    assert selection.dropped == (DroppedPair(a_across, BOTH_ENDS_PAIR, (("relayer", STRADDLES),)),)


def test_one_node_with_a_row_inside_and_a_row_straddling_drops_only_that_pair() -> None:
    """Casey, issue #55 comment 6985802330, item 6, the test he names.

    G2 used its own box with B, inside the tile, and a box across the west
    edge with C. Only C to G2 is dropped; B to G2 stays and G2 is written at
    its own box. For the counts check G2 touches the tile, so both pairs
    count as gateway-only pairs and the counts match.
    """
    inside, straddling = BOXED[1], across("C", "G2", 4, "gateway")
    found = links(BOXED[0], inside, straddling)
    assert tile_counts(counted_states(found, TILE))["only the gateway end"] == 2
    chosen = select_sites(found, TILE, DEM_BOUNDS_M)
    assert chosen.selection.dropped == (
        DroppedPair(straddling, GATEWAY_ONLY_PAIR, (("gateway", STRADDLES),)),
    )
    g2 = pseudonym("G2")
    assert [site.layer_id for site in chosen.sites()] == [pseudonym("G1"), f"{g2}.1"]
    assert [(site.layer_id, site.reason) for site in chosen.left_out] == [(f"{g2}.2", STRADDLES)]
    with pytest.raises(SelectionError, match="selected pairs dropped: gateway-only pair"):
        select(found, 3).layers()


def test_a_selected_gateway_failing_the_dem_check_drops_its_pair() -> None:
    """G3's own box lies inside the tile, but its box plus 50 m is not inside this DEM."""
    selection = select(links(*SELECTED), 3, NARROW_DEM_BOUNDS_M)
    assert selection.dem_check[pseudonym("G3")] is False
    assert selection.dropped == (
        DroppedPair(pair("C", "G3", 4), GATEWAY_ONLY_PAIR, (("gateway", FAILS_DEM),)),
    )


@pytest.mark.parametrize(
    ("pairs", "extent"),
    [
        ((BOXED[0], across("B", "G2", 5, "gateway"), BOXED[2]), DEM_BOUNDS_M),
        ((across("A", "G1", 2, "relayer"), BOXED[1], BOXED[2]), DEM_BOUNDS_M),
        (SELECTED, NARROW_DEM_BOUNDS_M),
    ],
)
def test_a_dropped_selected_pair_stops_the_layers_without_naming_a_node(
    pairs: tuple[PairActivity, ...], extent: Extent
) -> None:
    selection = select(links(*pairs), 3, extent)
    with pytest.raises(SelectionError) as refused:
        selection.layers()
    assert "selected pairs dropped" in str(refused.value)
    assert "engine_inputs Q1 (a)" in str(refused.value)
    assert not any(node.pseudonym in str(refused.value) for node in NODES.values())


def test_an_unselected_pair_with_a_straddling_row_is_dropped_alone() -> None:
    d_across = across("D", "E", 1, "relayer")
    states = pair_states(links(*BOXED, d_across), TILE)
    dem_check = {pseudonym(name): True for name in ("G1", "G2", "G3", "A")}
    assert dropped_pairs(states, KINDS, dem_check) == (
        DroppedPair(d_across, OTHER_PAIR, (("relayer", STRADDLES),)),
    )


def test_a_node_shared_with_a_dropped_pair_stays_in_the_kept_pair() -> None:
    kept, dropped = boxed("A", "G1", 2), across("D", "G1", 1, "relayer")
    states = pair_states(links(kept, dropped), TILE)
    found = dropped_pairs(states, {kept: BOTH_ENDS_PAIR}, {pseudonym("G1"): True})
    assert found == (DroppedPair(dropped, OTHER_PAIR, (("relayer", STRADDLES),)),)


def test_a_placed_row_failing_the_dem_check_drops_every_pair_that_used_it() -> None:
    selected, other = pair("C", "G3", 4), pair("D", "G3", 1)
    states = pair_states(links(selected, other), TILE)
    found = dropped_pairs(states, {selected: GATEWAY_ONLY_PAIR}, {pseudonym("G3"): False})
    assert found == (
        DroppedPair(selected, GATEWAY_ONLY_PAIR, (("gateway", FAILS_DEM),)),
        DroppedPair(other, OTHER_PAIR, (("gateway", FAILS_DEM),)),
    )


def test_straddle_drops_name_the_kind_when_the_tile_check_holds() -> None:
    g2_across = across("B", "G2", 5, "gateway")
    assert straddle_drops(links(BOXED[0], g2_across, BOXED[2]), TILE, 3) == (
        DroppedPair(g2_across, GATEWAY_ONLY_PAIR, (("gateway", STRADDLES),)),
    )


@pytest.mark.parametrize(
    ("dropped", "end", "expect_pairs"),
    [
        # A fourth touching pair.
        (across("D", "E", 1, "relayer"), "relayer", 4),
        # Two pairs with both ends in the tile.
        (across("B", "G2", 5, "relayer"), "relayer", 3),
        # The checks would pass but for the pair count.
        (across("B", "G2", 5, "gateway"), "gateway", 8),
    ],
)
def test_straddle_drops_leave_the_kind_open_when_the_tile_check_would_refuse(
    dropped: PairActivity, end: str, expect_pairs: int
) -> None:
    others = [p for p in BOXED if (p.relayer, p.gateway) != (dropped.relayer, dropped.gateway)]
    found = links(*others, dropped)
    assert straddle_drops(found, TILE, expect_pairs) == (
        DroppedPair(dropped, KIND_NOT_DETERMINED, ((end, STRADDLES),)),
    )


def test_straddle_drops_leave_the_dem_edge_check_to_the_selection() -> None:
    assert straddle_drops(links(*SELECTED), TILE, 3) == ()


def test_a_pair_failing_at_both_ends_names_both() -> None:
    both = across("A", "G3", 2, "relayer")
    states = pair_states(links(both), TILE)
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


def test_an_unselected_pair_with_a_straddling_row_fails_the_touching_count() -> None:
    """Counted as touching, the straddling relayer makes a fourth touching pair."""
    found = links(*BOXED, across("D", "E", 1, "relayer"))
    with pytest.raises(SelectionError, match="pairs touching x72y429 are not 3"):
        select(found, 4)


def test_a_straddling_gateway_only_relayer_fails_the_one_both_ends_pair_check() -> None:
    """Counted as touching, the relayer gives a gateway-only pair both ends in the tile."""
    found = links(BOXED[0], across("B", "G2", 5, "relayer"), BOXED[2])
    with pytest.raises(SelectionError, match="2 pairs have both ends in x72y429, not one"):
        select(found, 3)


def test_reproduction_a_a_both_ends_pair_by_the_counts_and_not_by_its_rows_refuses() -> None:
    """Reviewer, PR #70 comment 6989619559, Blocking 1 (a); interim guard until Casey answers.

    B's row with G1 lies outside the tile and its row with C inside. The
    counts read B to G1 as the both-ends pair and pass; by its own rows it is
    a gateway-only pair, so no receiver row exists. The strict run refuses
    with a SelectionError, not an IndexError, and names no node.
    """
    b_inside = (-10_000.0, 0.0)
    pairs = (boxed("B", "G1", 2), boxed("C", "B", 4, gateway_shift=b_inside), boxed("D", "G2", 1))
    found = links(*pairs, nodes=two_boxes("B", *b_inside))
    assert tile_counts(counted_states(found, TILE)) == {
        "both ends": 1,
        "only the gateway end": 2,
        "only the relayer end": 0,
        "touched": 3,
    }
    assert kind_conflicts(found, TILE) == (
        KindConflict(pairs[0], GATEWAY_ONLY_PAIR, BOTH_ENDS_PAIR),
    )
    with pytest.raises(SelectionError, match="kind by their own rows") as refused:
        select(found, 3)
    assert not any(node.pseudonym in str(refused.value) for node in NODES.values())


def test_reproduction_b_a_counted_gateway_only_pair_is_not_lost_silently() -> None:
    """Reviewer, PR #70 comment 6989619559, Blocking 1 (b); interim guard until Casey answers.

    G2's row with B lies inside the tile and its row with C 15 km east. The
    counts read C to G2 as the second gateway-only pair and pass; by its own
    rows it is no selected pair. The strict run refuses, and the conflict is
    there for a sites-only run to report.
    """
    g2_east = (15_000.0, 0.0)
    pairs = (boxed("A", "G1", 2), boxed("B", "G2", 5), boxed("C", "G2", 4, gateway_shift=g2_east))
    found = links(*pairs, nodes=two_boxes("G2", *g2_east))
    assert kind_conflicts(found, TILE) == (KindConflict(pairs[2], OTHER_PAIR, GATEWAY_ONLY_PAIR),)
    with pytest.raises(SelectionError, match="kind by their own rows"):
        select(found, 3)


def test_pairs_whose_kinds_agree_have_no_conflict() -> None:
    assert kind_conflicts(links(*BOXED), TILE) == ()


def test_layers_without_a_receiver_row_refuse_rather_than_fail() -> None:
    site = PlacedNode(pseudonym("G1"), "both_ends_gateway", 724_000.0, 4_285_000.0, 17.8, False)
    selection = Selection(placed=(site,), dem_check={pseudonym("G1"): True}, dropped=())
    with pytest.raises(SelectionError, match="no receiver row"):
        selection.layers()


@pytest.mark.parametrize(
    ("extent", "state"),
    [
        ((710_000.0, 4_285_000.0, 720_000.0, 4_285_100.0), "out"),
        ((720_000.0, 4_285_000.0, 720_100.0, 4_285_100.0), "in"),
        ((729_900.0, 4_289_900.0, 730_000.0, 4_290_000.0), "in"),
        ((729_900.0, 4_285_000.0, 730_001.0, 4_285_100.0), "split"),
        ((725_000.0, 4_279_900.0, 725_100.0, 4_280_000.0), "out"),
    ],
)
def test_a_box_whose_upper_edge_meets_the_tile_edge_only_touches_it(
    monkeypatch: pytest.MonkeyPatch, extent: Extent, state: str
) -> None:
    """Reviewer nit: a box's east and north edges are exclusive, so touching is not straddling."""
    monkeypatch.setattr(tile_selection, "box_extent_m", lambda _box: extent)
    assert row_state(cell_of(*NODES["G2"].box()), TILE) == state


def test_the_counts_check_runs_before_a_two_box_pair_is_dropped() -> None:
    """Casey, issue #55 comment 6985802330, item 3: counts first, then the two-box drop.

    C to G3 used G3's own box and a box 2 km west in its kept hours. Counted
    first, it is the second gateway-only pair, so the counts match; then it is
    dropped with MORE_THAN_ONE_BOX, and the strict run stops. Dropped first,
    the counts would read one gateway-only pair and refuse on that instead.
    """
    moved = MovedPair(
        pseudonym("C"), pseudonym("G3"), ("gateway",), 4, frozenset({cell_of(*NODES["C"].box())}),
        frozenset({cell_of(*NODES["G3"].box()), cell_of(*NODES["G3"].box(-2_000.0))}),
    )  # fmt: skip
    found = CaptureLinks(BOXED[:2], links(*BOXED[:2]).nodes, 0, 0, (moved,))
    selection = select(found, 3)
    assert selection.dropped == (
        DroppedPair(moved, GATEWAY_ONLY_PAIR, (("gateway", MORE_THAN_ONE_BOX),)),
    )
    assert pseudonym("G3") not in {node.pseudonym for node in selection.placed}
    with pytest.raises(SelectionError, match="selected pairs dropped: gateway-only pair"):
        selection.layers()


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


# G2 used its own box with B and a box 2 km east, still inside the tile, with C.
G2_EAST = (2_000.0, 0.0)
TWO_PLACES = (boxed("A", "G1", 2), boxed("B", "G2", 5), boxed("C", "G2", 4, gateway_shift=G2_EAST))


def test_a_node_with_one_box_keeps_its_pseudonym_as_its_row_id() -> None:
    ids = site_row_ids(links(*TWO_PLACES, nodes=two_boxes("G2", *G2_EAST)))
    assert ids[(pseudonym("G1"), cell_of(*NODES["G1"].box()).edges)] == pseudonym("G1")
    assert ids[(pseudonym("A"), cell_of(*NODES["A"].box()).edges)] == pseudonym("A")


def test_a_node_at_two_places_gets_a_row_id_per_box_in_pair_order() -> None:
    """Casey, issue #44, 2026-11-17: "a moving node is placed per pair"."""
    ids = site_row_ids(links(*TWO_PLACES, nodes=two_boxes("G2", *G2_EAST)))
    own, west = cell_of(*NODES["G2"].box()), cell_of(*NODES["G2"].box(*G2_EAST))
    assert ids[(pseudonym("G2"), own.edges)] == f"{pseudonym('G2')}.1"
    assert ids[(pseudonym("G2"), west.edges)] == f"{pseudonym('G2')}.2"


def test_a_gateway_at_two_places_is_one_site_per_pair() -> None:
    found = links(*TWO_PLACES, nodes=two_boxes("G2", *G2_EAST))
    selection = select(found, 3)
    sites, receiver = selection.layers()
    assert [(site.layer_id, site.role) for site in sites] == [
        (pseudonym("G1"), "both_ends_gateway"),
        (f"{pseudonym('G2')}.1", "gateway_only_gateway"),
        (f"{pseudonym('G2')}.2", "gateway_only_gateway"),
    ]
    assert {site.pseudonym for site in sites[1:]} == {pseudonym("G2")}
    assert (sites[2].easting_m, sites[2].northing_m) == pytest.approx(
        box_centre_m(NODES["G2"].box(*G2_EAST)), abs=1e-3
    )
    assert receiver.layer_id == pseudonym("A")
    assert selection.dropped == ()
    assert set(selection.dem_check) == {site.layer_id for site in (*sites, receiver)}


def test_a_receiver_at_two_places_takes_the_box_of_the_both_ends_pair() -> None:
    nodes = two_boxes("A", 2_000.0, 0.0)
    found = links(boxed("A", "G1", 2), boxed("B", "G2", 5), boxed("C", "G3", 4), nodes=nodes)
    receiver = select(found, 3).layers()[1]
    assert receiver.layer_id == f"{pseudonym('A')}.1"
    assert (receiver.easting_m, receiver.northing_m) == pytest.approx(
        box_centre_m(NODES["A"].box()), abs=1e-3
    )


def test_a_row_failing_the_dem_check_drops_only_the_pair_that_used_it() -> None:
    """G3 used its own box with C, beyond this DEM's margin, and a box 2 km west with D."""
    west = (-2_000.0, 0.0)
    pairs = (boxed("A", "G1", 2), boxed("C", "G3", 4), boxed("D", "G3", 1, gateway_shift=west))
    nodes = two_boxes("G3", *west)
    chosen = select_sites(links(*pairs, nodes=nodes), TILE, NARROW_DEM_BOUNDS_M)
    assert chosen.selection.dropped == (
        DroppedPair(pairs[1], GATEWAY_ONLY_PAIR, (("gateway", FAILS_DEM),)),
    )
    assert [(site.layer_id, site.reason) for site in chosen.left_out] == [
        (f"{pseudonym('G3')}.1", FAILS_DEM)
    ]
    assert [site.layer_id for site in chosen.sites()] == [
        pseudonym("G1"),
        f"{pseudonym('G3')}.2",
    ]


def test_refuses_a_pair_without_its_box_for_a_node_at_two_places_without_naming_it() -> None:
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
    assert "carries no relayer box" in str(refused.value)
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
