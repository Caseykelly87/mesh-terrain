"""The terrain exception classes stay outside ValueError (ADR 0006 item 11)."""

import pytest

from terrain.errors import OutOfExtentError, RefusedDrawError, TerrainError


def test_neither_terrain_error_is_a_value_error() -> None:
    assert not issubclass(OutOfExtentError, ValueError)
    assert not issubclass(RefusedDrawError, ValueError)
    assert issubclass(OutOfExtentError, TerrainError)
    assert issubclass(RefusedDrawError, TerrainError)


def test_a_value_error_catch_does_not_take_a_refusal() -> None:
    with pytest.raises(RefusedDrawError):
        try:
            raise RefusedDrawError("dem_gap", "NaN at profile index 40")
        except ValueError:
            pytest.fail("a ValueError handler caught a refused draw")


def test_refusal_carries_its_reason() -> None:
    refusal = RefusedDrawError("empty_cell", "cell (3, 4) has a pixel without a value")

    assert refusal.reason == "empty_cell"
    assert "cell (3, 4)" in str(refusal)


def test_refusal_rejects_an_unknown_reason() -> None:
    with pytest.raises(ValueError):
        RefusedDrawError("tired", "no")  # type: ignore[arg-type]
