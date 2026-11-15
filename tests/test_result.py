"""The per-draw result record: the cap test behind p1812_at_cap_share (ADR 0006 item 2 (a))."""

import pytest

from terrain.result import CAP_TOLERANCE_DB, PathLossResult


def test_models_without_lbs_have_no_cap_answer() -> None:
    assert PathLossResult(loss_db=151.76, itm_warning_flags=0).at_troposcatter_cap is None


@pytest.mark.parametrize(
    ("loss_db", "at_cap"),
    [(160.17, True), (160.07, True), (160.17 - CAP_TOLERANCE_DB - 0.01, False), (149.48, False)],
)
def test_cap_is_within_a_tenth_of_a_decibel_of_lbs(loss_db: float, at_cap: bool) -> None:
    result = PathLossResult(loss_db=loss_db, troposcatter_loss_db=160.17)
    assert result.at_troposcatter_cap is at_cap
