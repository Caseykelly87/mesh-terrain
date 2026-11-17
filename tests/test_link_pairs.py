"""The both-ends pairs of a capture snapshot and its nodes' positions, on the synthetic capture.

No test reads the real snapshot.
"""

import math
import sys
from pathlib import Path

import pytest
from engine_fixture import no_logger_caching
from engine_inputs_fixture import (
    COARSE_BITS,
    NODES,
    OUTSIDE_MSA_SHIFT_M,
    WIDE_BITS,
    build_capture,
    position_row,
)

from ingester import link_pairs
from ingester.link_pairs import (
    Box,
    NodePositions,
    SelectionError,
    box_half_width_m,
    capture_links,
    msa_contains,
    node_positions,
    read_capture_rows,
)

# The import above activates the autouse fixture for this module.
_ = no_logger_caching

SPIKE_DIR = Path(__file__).resolve().parents[1] / "docs" / "adr" / "0003-spike"


def pseudonym(name: str) -> str:
    return NODES[name].pseudonym


@pytest.fixture
def capture(tmp_path: Path) -> Path:
    return build_capture(tmp_path / "snapshots")


def test_pairs_follow_the_stationarity_rule(capture: Path) -> None:
    links = capture_links(read_capture_rows(capture))
    pairs = {(pair.relayer, pair.gateway): pair for pair in links.pairs}
    expected = {("A", "G1"), ("B", "G2"), ("C", "G3"), ("D", "E"), ("M", "E")}
    assert set(pairs) == {(pseudonym(r), pseudonym(g)) for r, g in expected}
    assert links.pair_counts == {"public": 4, "meshstl": 2, "shared": 1, "union": 5}
    busy = pairs[(pseudonym("B"), pseudonym("G2"))]
    assert (busy.link_hours_public, busy.link_hours_meshstl) == (4, 1)
    assert (busy.link_hours_union, busy.receipts) == (4, 15)


def test_a_coarse_node_makes_no_pair(capture: Path) -> None:
    links = capture_links(read_capture_rows(capture))
    assert all(pseudonym("F") not in (pair.relayer, pair.gateway) for pair in links.pairs)


def test_a_moving_node_is_not_stationary(capture: Path) -> None:
    nodes = capture_links(read_capture_rows(capture)).nodes
    assert len(nodes[pseudonym("M")].boxes) == 2
    assert nodes[pseudonym("M")].stationary_box is None
    assert nodes[pseudonym("G2")].stationary_box == Box(*NODES["G2"].box())


def test_positions_at_zero_or_with_one_coordinate_are_not_positions(capture: Path) -> None:
    links = capture_links(read_capture_rows(capture))
    assert links.nodes[pseudonym("A")].boxes == frozenset({Box(*NODES["A"].box())})
    assert links.one_coordinate_missing == 1


def test_precise_source_needs_twenty_bits_from_meshstl(capture: Path) -> None:
    nodes = capture_links(read_capture_rows(capture)).nodes
    assert nodes[pseudonym("G1")].precise_source
    assert not nodes[pseudonym("A")].precise_source
    assert not nodes[pseudonym("G2")].precise_source


def test_a_node_with_nested_boxes_from_both_brokers_is_stationary_at_the_smallest(
    tmp_path: Path,
) -> None:
    """G1's 15-bit public box holds its 20-bit MeshSTL box (lnk_02, Overlap amendment)."""
    links = capture_links(read_capture_rows(build_capture(tmp_path / "s", nested_g1=True)))
    g1 = links.nodes[pseudonym("G1")]
    assert g1.boxes == {Box(*NODES["G1"].box()), Box(*NODES["G1"].box(bits=WIDE_BITS))}
    assert g1.stationary_box == Box(*NODES["G1"].box())
    assert g1.precise_source


def test_boxes_outside_the_msa_and_coarse_boxes_are_not_read(tmp_path: Path) -> None:
    links = capture_links(read_capture_rows(build_capture(tmp_path / "s", ignored_a=True)))
    assert links.nodes[pseudonym("A")].boxes == {Box(*NODES["A"].box())}
    assert links.nodes[pseudonym("A")].stationary_box == Box(*NODES["A"].box())


def test_a_coarse_node_has_no_positions(capture: Path) -> None:
    assert pseudonym("F") not in capture_links(read_capture_rows(capture)).nodes


def test_a_node_whose_only_boxes_lie_outside_the_msa_has_no_positions() -> None:
    outside = NODES["G3"].box(east_shift_m=OUTSIDE_MSA_SHIFT_M)
    rows = [position_row("G3", outside), position_row("C", NODES["C"].box())]
    found = node_positions(rows, msa_contains())
    assert set(found) == {pseudonym("C")}


def test_node_positions_read_only_usable_boxes_in_the_area() -> None:
    """The area test is passed in; here it keeps boxes west of a line about 3.5 km east of G1."""
    west, east = NODES["G1"].box(), NODES["G1"].box(east_shift_m=50_000)
    coarse = NODES["G1"].box(bits=COARSE_BITS)
    rows = [position_row("G1", place, meshstl=True) for place in (west, east, coarse)]
    found = node_positions(rows, lambda _lat, lon: lon < west[1] + 400_000)
    assert found == {pseudonym("G1"): NodePositions(frozenset({Box(*west)}), True)}


def test_precise_source_reads_only_boxes_in_the_area() -> None:
    """A 20-bit MeshSTL box outside the area does not make the node a precise source."""
    usable = NODES["A"].box()
    outside = NODES["A"].box(east_shift_m=OUTSIDE_MSA_SHIFT_M, bits=20)
    rows = [position_row("A", usable), position_row("A", outside, meshstl=True)]
    assert not node_positions(rows, msa_contains())[pseudonym("A")].precise_source


def test_touching_boxes_make_a_node_moved() -> None:
    latitude_i, longitude_i, bits = NODES["A"].box()
    east = Box(latitude_i, longitude_i + 2 ** (32 - bits), bits)
    node = NodePositions(frozenset({Box(latitude_i, longitude_i, bits), east}), False)
    assert node.stationary_box is None


def test_two_different_32_bit_readings_make_a_node_moved() -> None:
    latitude_i, longitude_i, _ = NODES["A"].box()
    boxes = frozenset({Box(latitude_i, longitude_i, 32), Box(latitude_i, longitude_i + 1, 32)})
    assert NodePositions(boxes, False).stationary_box is None


def test_a_tie_for_the_smallest_box_is_refused_without_a_coordinate() -> None:
    """Off-grid boxes of one precision can overlap; the rule then names no single box."""
    latitude_i, longitude_i, bits = NODES["A"].box()
    boxes = frozenset({Box(latitude_i, longitude_i, bits), Box(latitude_i + 9, longitude_i, bits)})
    with pytest.raises(SelectionError, match="most precision bits") as refused:
        _ = NodePositions(boxes, False).stationary_box
    assert str(latitude_i) not in str(refused.value)


@pytest.mark.parametrize(
    "boxes", [frozenset(), frozenset({Box(*NODES["F"].box())})], ids=["none", "coarse"]
)
def test_node_positions_hold_only_usable_boxes(boxes: frozenset[Box]) -> None:
    with pytest.raises(ValueError, match="usable"):
        NodePositions(boxes, False)


def _gate_pairs(folder: Path) -> dict[str, set[tuple[str, str]]]:
    """The reproduction gate's pairs per run, its stand-ins mapped back to pseudonyms."""
    try:
        sys.path.insert(0, str(SPIKE_DIR))
        import reproduce_from_snapshot as gate

        snapshot = gate.Snapshot(folder)
        runs = {"public": "public_full", "meshstl": "meshstl_24h"}
        found = {broker: gate.stationary_pairs(snapshot, run) for broker, run in runs.items()}
        # Stand-ins are given out as the runs read the rows, so map them back afterwards.
        names = {stand_in: name for name, stand_in in snapshot.ids.ids.items()}
        return {
            broker: {(names[relayer], names[gateway]) for relayer, gateway in pairs}
            for broker, pairs in found.items()
        }
    finally:
        # The gate puts its own folder on the path too; take every copy off.
        sys.path[:] = [entry for entry in sys.path if Path(entry).resolve() != SPIKE_DIR]


def _own_pairs(folder: Path) -> dict[str, set[tuple[str, str]]]:
    pairs = capture_links(read_capture_rows(folder)).pairs
    return {
        "public": {(p.relayer, p.gateway) for p in pairs if p.link_hours_public},
        "meshstl": {(p.relayer, p.gateway) for p in pairs if p.link_hours_meshstl},
    }


def test_pairs_equal_the_reproduction_gates(tmp_path: Path) -> None:
    folder = build_capture(tmp_path / "snapshots", one_coordinate=False)
    assert _own_pairs(folder) == _gate_pairs(folder)


def test_pairs_are_a_superset_of_the_gates_when_a_coordinate_is_missing(tmp_path: Path) -> None:
    """With a one-coordinate Position, the command finds every gate pair and more.

    The gate reads the missing coordinate as 0, so the row becomes a box at
    latitude 0 or longitude 0. That box lies outside the MSA and the spike's
    keep box, so it adds no usable position and only adds a box to the node:
    it can make a stationary node non-stationary, never the reverse. Every
    link-hour the gate finds therefore stands under the contract's reading,
    which can only find more. In the fixture, A sends one such row, so the gate
    loses A's stationary box and with it the pair A to G1.
    """
    folder = build_capture(tmp_path / "snapshots", one_coordinate=True)
    own, gate = _own_pairs(folder), _gate_pairs(folder)
    assert own["public"] == gate["public"]
    assert own["meshstl"] > gate["meshstl"]
    assert own["meshstl"] - gate["meshstl"] == {(pseudonym("A"), pseudonym("G1"))}


def test_missing_spike_scripts_are_refused(
    tmp_path: Path, capture: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = read_capture_rows(capture)
    monkeypatch.setattr(link_pairs, "SPIKE_DIR", tmp_path / "no-spike")
    with pytest.raises(SelectionError, match="analyze.py, figures.py not found"):
        capture_links(rows)


@pytest.mark.parametrize("bits", [13, 16, 18, 20])
def test_box_half_width_is_the_inscribed_square(bits: int) -> None:
    """Casey, issue #44, 1 (b): "an inscribed square that trims the long side"."""
    latitude_deg = 38.7
    north_south_m = 2 ** (31 - bits) * 1e-7 * 111_320.0
    east_west_m = north_south_m * math.cos(math.radians(latitude_deg))
    assert box_half_width_m(bits, latitude_deg) == pytest.approx(east_west_m, abs=1e-9)
    assert box_half_width_m(bits, latitude_deg) < north_south_m
