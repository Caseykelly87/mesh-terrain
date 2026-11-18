"""The committed link-rules seeds are a faithful copy of the hand-worked cases."""

import csv
import io
from pathlib import Path

import pytest
from fixture_rows import LINK_RULES_CASES, read_cases
from link_rules_expected import HOURS_SEED, RECEIPTS_SEED, box_columns, link_cases, seeds


@pytest.mark.parametrize("path", [RECEIPTS_SEED, HOURS_SEED], ids=lambda path: path.name)
def test_the_committed_seed_is_what_the_cases_give(path: Path) -> None:
    assert path.read_text(encoding="utf-8") == seeds()[path], (
        "stale seed: run uv run python tests/link_rules_expected.py"
    )


def seed_rows(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))


def test_every_expected_receipt_and_link_hour_is_in_a_seed() -> None:
    cases = link_cases()
    receipts = sum(len(case["expected_receipts"]) for case in cases)
    hours = sum(len(case["expected_link_hours"]) for case in cases)
    assert (len(seed_rows(RECEIPTS_SEED)), len(seed_rows(HOURS_SEED))) == (receipts, hours)
    assert len(read_cases(LINK_RULES_CASES)) == 51
    assert [case["case_id"][:3] for case in cases[51:]] == [f"s{n:02d}" for n in range(5, 13)]


def test_the_seeds_hold_the_two_one_box_drops_and_three_contradicted_direct_hops() -> None:
    hours = seed_rows(HOURS_SEED)
    one_box = sorted(
        row["case_id"] for row in hours if row["rule"] == "lnk_02" and row["case_id"][0] == "c"
    )
    assert one_box == [
        "c24_q6_two_fallback_boxes_drop_the_hour",
        "c42_gateway_two_fallback_boxes_drop_the_hour",
    ]
    contradicted = [
        row
        for row in seed_rows(RECEIPTS_SEED)
        if row["case_id"].startswith("c48") and row["rule"] == "lnk_01"
    ]
    assert len(contradicted) == 3


def test_a_box_the_case_does_not_define_stops_the_seed() -> None:
    case = {"case_id": "cXX", "boxes": {}}
    with pytest.raises(ValueError, match="not in the case's boxes"):
        box_columns(case, {"relayer_box": "r20"})
