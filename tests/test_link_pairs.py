"""The pairs of a capture snapshot under the contract's link-hour rules, on the synthetic capture.

Each pair carries the box its counted receipts used at each end (Casey, issue
#44, 2026-11-17); a pair whose end used more than one box is dropped and
reported. No test reads the real snapshot.
"""

import math
from pathlib import Path
from typing import Any

import pytest
from engine_fixture import no_logger_caching
from engine_inputs_fixture import NODES, build_capture

from ingester.link_cells import LinkBox, cell_of
from ingester.link_pairs import (
    CaptureLinks,
    NodePositions,
    PairActivity,
    capture_links,
    read_capture_rows,
)

# The import above activates the autouse fixture for this module.
_ = no_logger_caching


def pseudonym(name: str) -> str:
    return NODES[name].pseudonym


def own_box(name: str) -> LinkBox:
    return cell_of(*NODES[name].box())


def links_of(tmp_path: Path, **options: Any) -> CaptureLinks:
    return capture_links(read_capture_rows(build_capture(tmp_path / "s", **options)))


def by_names(links: CaptureLinks) -> dict[tuple[str, str], PairActivity]:
    names = {node.pseudonym: name for name, node in NODES.items()}
    return {(names[p.relayer], names[p.gateway]): p for p in links.pairs}


@pytest.fixture
def links(tmp_path: Path) -> CaptureLinks:
    return links_of(tmp_path)


def test_pairs_follow_the_contract_on_the_merged_snapshot(links: CaptureLinks) -> None:
    pairs = by_names(links)
    assert set(pairs) == {("A", "G1"), ("B", "G2"), ("C", "G3"), ("D", "E"), ("M", "E")}
    assert links.pair_counts == {"pairs": 5, "placed": 5, "more than one box": 0}
    busy = pairs[("B", "G2")]
    assert (busy.link_hours, busy.link_hours_public, busy.link_hours_meshstl) == (4, 4, 1)
    assert busy.receipts == 12


def test_each_pair_carries_the_box_its_counted_receipts_used(links: CaptureLinks) -> None:
    pairs = by_names(links)
    for (relayer, gateway), pair in pairs.items():
        assert pair.relayer_box == own_box(relayer)
        assert pair.gateway_box == own_box(gateway)


def test_a_moving_node_is_placed_at_the_box_its_pairs_receipts_used(
    links: CaptureLinks,
) -> None:
    """M sends two boxes 1.7 km apart; its hour's counted receipts all use the first."""
    node = links.nodes[pseudonym("M")]
    assert node.one_box == own_box("M")
    assert node.boxes == frozenset({own_box("M")})


def test_a_coarse_node_makes_no_pair_and_has_no_box(links: CaptureLinks) -> None:
    assert all(pseudonym("F") not in (pair.relayer, pair.gateway) for pair in links.pairs)
    assert pseudonym("F") not in links.nodes


def test_positions_at_zero_or_with_one_coordinate_are_not_read(links: CaptureLinks) -> None:
    assert links.nodes[pseudonym("A")].boxes == frozenset({own_box("A")})
    assert links.one_coordinate_missing == 1


def test_precise_source_needs_twenty_bits_from_meshstl(links: CaptureLinks) -> None:
    pairs = by_names(links)
    assert pairs[("A", "G1")].gateway_precise_source
    assert not pairs[("A", "G1")].relayer_precise_source
    assert links.nodes[pseudonym("G1")].precise_source
    assert not links.nodes[pseudonym("G2")].precise_source


def test_nested_boxes_from_both_brokers_place_the_node_at_the_finer(tmp_path: Path) -> None:
    """G1's 15-bit public box holds its 20-bit MeshSTL box (lnk_02)."""
    g1 = links_of(tmp_path, nested_g1=True).nodes[pseudonym("G1")]
    assert g1.one_box == own_box("G1")
    assert g1.precise_source


def test_boxes_outside_the_msa_and_coarse_boxes_are_not_read(tmp_path: Path) -> None:
    assert links_of(tmp_path, ignored_a=True).nodes[pseudonym("A")].one_box == own_box("A")


def test_a_pair_whose_end_used_two_boxes_is_dropped_and_reported(tmp_path: Path) -> None:
    links = links_of(tmp_path, moved_in_pair=True)
    assert ("C", "G3") not in by_names(links)
    assert links.pair_counts == {"pairs": 5, "placed": 4, "more than one box": 1}
    (moved,) = links.moved_pairs
    assert (moved.relayer, moved.gateway, moved.ends) == (
        pseudonym("C"),
        pseudonym("G3"),
        ("gateway",),
    )
    assert pseudonym("G3") not in links.nodes


def test_a_node_with_a_different_box_in_two_pairs_has_both(tmp_path: Path) -> None:
    links = links_of(tmp_path, moved_g3=True)
    g3 = links.nodes[pseudonym("G3")]
    assert len(g3.boxes) == 2
    assert g3.one_box is None
    assert links.pair_counts["pairs"] == 6


def test_the_data_quality_counts_are_carried(links: CaptureLinks) -> None:
    assert (links.direct_hop_contradicted, links.one_box_dropped_hours) == (0, 0)


def test_node_positions_need_a_box() -> None:
    with pytest.raises(ValueError, match="at least one box"):
        NodePositions(frozenset(), False)


@pytest.mark.parametrize("bits", [16, 18, 20])
def test_the_placed_half_width_is_the_inscribed_square_by_pos_01(bits: int) -> None:
    """Casey, issue #44, 1 (b): "an inscribed square that trims the long side"."""
    latitude_i, longitude_i, _ = NODES["A"].box()
    box = cell_of(latitude_i, longitude_i, bits)
    assert box.smaller_half_width_m == box.half_width_ew_m < box.half_width_ns_m
    radius_m = 6_378_137.0
    rough_m = (
        2 ** (31 - bits)
        * 1e-7
        * math.pi
        / 180
        * radius_m
        * math.cos(math.radians(latitude_i / 1e7))
    )
    assert box.smaller_half_width_m == pytest.approx(rough_m, rel=1e-2)
