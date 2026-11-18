"""The expected-positions seed is current, and its computed parts agree with the hand-worked values.

`tests/fixture_positions.py` writes the seed the dbt fixture tests compare
`stg_position` and `core.fct_node_position` with. Its quality, 0, 0, precise,
MSA and core columns are copied from rows written by hand; these tests check
that every fixture position has one, and that an independent Python
computation of the same columns agrees with every hand row. Cells and
half-widths are computed, and checked against the boxes worked out by hand in
the link-rules cases. These tests read files only.
"""

import csv
import io
import math

from fixture_positions import (
    HAND_COLUMNS,
    SEED,
    cell_low,
    cell_width,
    computed_positions,
    expected_positions,
    half_widths,
    hand_rows,
    inside_msa,
    quality,
    seed_text,
)
from fixture_rows import LINK_RULES_CASES, read_cases, staging_rows

HALF_WIDTH_TOLERANCE_M = 1e-6
EDGES = ("south_i", "north_i", "west_i", "east_i")


def test_the_committed_seed_is_what_the_fixtures_give() -> None:
    expected = seed_text(expected_positions())
    assert SEED.read_text(encoding="utf-8") == expected, (
        "dbt/seeds/fixtures/expected_fixture_positions.csv is stale; "
        "run uv run python tests/fixture_positions.py"
    )


def test_every_fixture_position_has_a_hand_written_row_and_no_hand_row_is_spare() -> None:
    computed = {(row["snapshot_id"], row["delivery_key"]) for row in computed_positions()}
    assert computed == set(hand_rows())


def test_the_python_computation_agrees_with_every_hand_written_row() -> None:
    # A disagreement is a question about the contract, settled by hand, never
    # by changing a hand row to match the code.
    hand = hand_rows()
    disagreements = [
        (row["snapshot_id"], row["delivery_key"], column, row[column], written[column])
        for row in computed_positions()
        for written in [hand[(row["snapshot_id"], row["delivery_key"])]]
        for column in HAND_COLUMNS
        if row[column] != written[column]
    ]
    assert disagreements == []


def test_the_seed_has_one_row_per_fixture_position_row() -> None:
    rows = list(csv.DictReader(io.StringIO(SEED.read_text(encoding="utf-8"))))
    keys = [(row["snapshot_id"], row["delivery_key"]) for row in rows]
    assert len(keys) == len(set(keys)) > 140


def test_every_single_cell_box_worked_by_hand_is_the_cell_of_a_position_in_its_case() -> None:
    positions = expected_positions()
    for case in read_cases(LINK_RULES_CASES):
        prefix = case["case_id"][:3] + "-"
        cells = [p for p in positions if p["delivery_key"].startswith(prefix) and p["cell_width_i"]]
        for name, box in case["boxes"].items():
            if box["merged"]:
                continue
            matches = [
                cell
                for cell in cells
                if all(cell[edge] == box[edge] for edge in EDGES)
                and cell["precision_bits"] == box["precision_bits"]
            ]
            assert matches, f"{case['case_id']} box {name} is no position's cell"
            for cell in matches:
                for side in ("half_width_ns_m", "half_width_ew_m"):
                    assert math.isclose(cell[side], box[side], abs_tol=HALF_WIDTH_TOLERANCE_M)


def test_every_merged_box_edge_is_an_edge_of_a_usable_cell_in_its_case() -> None:
    positions = expected_positions()
    for case in read_cases(LINK_RULES_CASES):
        prefix = case["case_id"][:3] + "-"
        usable = [
            p
            for p in positions
            if p["delivery_key"].startswith(prefix) and p["position_quality"] == "usable"
        ]
        for name, box in case["boxes"].items():
            if box["merged"]:
                for edge in EDGES:
                    assert any(cell[edge] == box[edge] for cell in usable), (case["case_id"], name)


def test_the_msa_edge_positions_tell_the_stored_value_from_the_cell_centre() -> None:
    # Casey's Q3: pos_02 tests a 32-bit position's stored value. Each s02 row
    # must land on the other side of the edge when read at its cell centre,
    # or the case could not tell the two readings apart.
    edge_rows = [row for row in staging_rows() if row["delivery_key"].startswith("s02-")]
    assert len(edge_rows) == 2
    answers = set()
    for row in edge_rows:
        stored = inside_msa(row["latitude_i"], row["longitude_i"])
        centre = inside_msa(row["latitude_i"] + 0.5, row["longitude_i"] + 0.5)
        assert stored != centre
        answers.add(stored)
    assert answers == {True, False}


def test_quality_follows_pos_01() -> None:
    assert [quality(bits) for bits in (None, -1, 0, 11, 12, 14, 15, 32, 33)] == [
        "unusable",
        "unusable",
        "unusable",
        "unusable",
        "coarse",
        "coarse",
        "usable",
        "usable",
        "unusable",
    ]


def test_cell_width_is_two_to_the_missing_bits_and_none_outside_1_to_32() -> None:
    assert cell_width(32) == 1
    assert cell_width(16) == 65536
    assert cell_width(1) == 2**31
    assert [cell_width(bits) for bits in (None, -1, 0, 33)] == [None, None, None, None]


def test_the_low_edge_is_half_a_width_below_the_centre_except_at_32_bits() -> None:
    assert cell_low(385974272, 16, 65536) == 385941504
    assert cell_low(385999949, 32, 1) == 385999949


def test_half_widths_match_the_hand_worked_16_bit_cell() -> None:
    # c01's g16 box: 363.750397 m north-south and 285.458876 m east-west.
    north_south, east_west = half_widths(65536, 385974272)
    assert math.isclose(north_south, 363.750397, abs_tol=HALF_WIDTH_TOLERANCE_M)
    assert math.isclose(east_west, 285.458876, abs_tol=HALF_WIDTH_TOLERANCE_M)
