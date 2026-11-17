"""The amended lnk_02 overlap test on position boxes, in whole numbers.

Every box is invented. A box is (latitude_i, longitude_i, precision_bits):
1e-7 degree integers, stored as the centre of the box for 15 to 31 bits, with
a half-width of 2^(31 - precision_bits) units on each axis.
"""

import pytest

from ingester.box_overlap import TiedBoxesError, boxes_overlap, stationary_box


def cell(latitude_i: int, longitude_i: int, bits: int) -> tuple[int, int, int]:
    """The box of the given precision that holds the point, centred as firmware centres it."""
    shift = 32 - bits
    if shift == 0:
        return latitude_i, longitude_i, bits
    half = 1 << (shift - 1)
    return ((latitude_i >> shift) << shift) + half, ((longitude_i >> shift) << shift) + half, bits


# An invented point near St. Louis: the longitude is negative.
LATITUDE_I, LONGITUDE_I = 386_123_457, -903_456_789


@pytest.mark.parametrize(("coarse", "fine"), [(15, 20), (15, 16), (16, 18), (18, 32), (15, 32)])
def test_nested_cells_at_different_precisions_overlap(coarse: int, fine: int) -> None:
    big, small = cell(LATITUDE_I, LONGITUDE_I, coarse), cell(LATITUDE_I, LONGITUDE_I, fine)
    assert big != small
    assert boxes_overlap(big, small)
    assert boxes_overlap(small, big)


@pytest.mark.parametrize("bits", [15, 16, 20])
def test_neighbouring_cells_of_one_precision_only_touch(bits: int) -> None:
    width = 1 << (32 - bits)
    here = cell(LATITUDE_I, LONGITUDE_I, bits)
    east = (here[0], here[1] + width, bits)
    north = (here[0] + width, here[1], bits)
    assert not boxes_overlap(here, east)
    assert not boxes_overlap(here, north)


def test_a_fine_cell_touching_a_coarse_cell_from_outside_does_not_overlap() -> None:
    """A 20-bit cell just west of a 16-bit cell's west edge, at a negative longitude."""
    coarse = cell(LATITUDE_I, LONGITUDE_I, 16)
    west_edge = coarse[1] - (1 << 15)
    fine = cell(coarse[0], west_edge - 1, 20)
    assert fine[1] + (1 << 11) == west_edge
    assert not boxes_overlap(coarse, fine)
    inside = cell(coarse[0], west_edge, 20)
    assert boxes_overlap(coarse, inside)


def test_boxes_one_unit_short_of_touching_overlap() -> None:
    """The half-widths at 16 bits sum to 65 536 units: 65 535 apart overlaps, 65 536 touches."""
    here = (LATITUDE_I, LONGITUDE_I, 16)
    assert boxes_overlap(here, (LATITUDE_I, LONGITUDE_I - 65_535, 16))
    assert not boxes_overlap(here, (LATITUDE_I, LONGITUDE_I - 65_536, 16))


def test_boxes_must_overlap_on_both_axes() -> None:
    here = cell(LATITUDE_I, LONGITUDE_I, 16)
    assert not boxes_overlap(here, (here[0] + 10, here[1] + (1 << 16), 16))
    assert not boxes_overlap(here, (here[0] + (1 << 16), here[1] + 10, 16))


def test_two_32_bit_readings_overlap_only_when_equal() -> None:
    """A 32-bit box has a half-width of half a unit, so the doubled test stays in whole numbers."""
    reading = (LATITUDE_I, LONGITUDE_I, 32)
    assert boxes_overlap(reading, reading)
    assert not boxes_overlap(reading, (LATITUDE_I, LONGITUDE_I + 1, 32))
    assert not boxes_overlap(reading, (LATITUDE_I - 1, LONGITUDE_I, 32))


@pytest.mark.parametrize("bits", [-1, 33])
def test_precision_outside_0_to_32_bits_is_refused(bits: int) -> None:
    with pytest.raises(ValueError, match="precision_bits"):
        boxes_overlap((LATITUDE_I, LONGITUDE_I, bits), (LATITUDE_I, LONGITUDE_I, 16))


def test_nested_boxes_are_stationary_at_the_smallest() -> None:
    boxes = [cell(LATITUDE_I, LONGITUDE_I, bits) for bits in (15, 20, 16)]
    assert stationary_box(boxes) == cell(LATITUDE_I, LONGITUDE_I, 20)


def test_one_box_is_stationary_at_itself() -> None:
    box = cell(LATITUDE_I, LONGITUDE_I, 16)
    assert stationary_box([box]) == box


def test_no_box_is_not_stationary() -> None:
    assert stationary_box([]) is None


def test_touching_boxes_are_not_stationary() -> None:
    here = cell(LATITUDE_I, LONGITUDE_I, 16)
    assert stationary_box([here, (here[0], here[1] + (1 << 16), 16)]) is None


def test_boxes_that_do_not_all_overlap_are_not_stationary() -> None:
    """Each fine box lies in the coarse one, but the two fine boxes do not overlap each other."""
    coarse = cell(LATITUDE_I, LONGITUDE_I, 15)
    west = cell(coarse[0], coarse[1] - 10_000, 20)
    east = cell(coarse[0], coarse[1] + 10_000, 20)
    assert boxes_overlap(coarse, west) and boxes_overlap(coarse, east)
    assert stationary_box([coarse, west, east]) is None


def test_two_different_32_bit_readings_are_not_stationary() -> None:
    readings = [(LATITUDE_I, LONGITUDE_I, 32), (LATITUDE_I, LONGITUDE_I + 7, 32)]
    assert stationary_box(readings) is None


def test_overlapping_boxes_tied_for_the_most_bits_are_refused() -> None:
    """Firmware cells of one precision never overlap unless equal; off-grid boxes can."""
    first = (LATITUDE_I, LONGITUDE_I, 16)
    second = (LATITUDE_I + 100, LONGITUDE_I, 16)
    coarse = cell(LATITUDE_I, LONGITUDE_I, 15)
    with pytest.raises(TiedBoxesError, match="most precision bits"):
        stationary_box([coarse, first, second])
