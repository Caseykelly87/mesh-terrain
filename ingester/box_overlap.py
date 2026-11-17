"""Whether position boxes are one place under the amended `lnk_02` rule, in whole numbers.

A node that keeps fewer than 32 bits of its position sends a box: the
firmware stores the box's centre for 15 to 31 bits, and the box reaches
2^(31 - precision_bits) units of 1e-7 degree either side of it on each axis
(ADR 0003, Overlap amendment, 2026-11-17). Two boxes overlap when their
centres differ by less than the sum of the two half-widths in latitude and in
longitude; boxes that only touch do not overlap (Casey, PR #52, 3:
"whole-number arithmetic, touching boxes do not overlap"). A 32-bit box has a
half-width of half a unit, so the test is written doubled and stays in
integers:

    2 * abs(a - b) < 2**(32 - bits_a) + 2**(32 - bits_b)

Worked example at 16 bits, a box about 730 m north to south at St. Louis:
each half-width is 32 768 units, so two 16-bit boxes overlap when their
centres are under 65 536 units apart on both axes. Neighbouring 16-bit cells
are exactly 65 536 apart: they touch and do not overlap. A 20-bit box inside
a 16-bit box is at most 32 768 - 2 048 units from its centre, under the sum
of 34 816, so it overlaps.

A node is stationary when it sent at least one usable box and all of them
overlap one another; it is placed at its smallest box, the one with the most
precision bits. Firmware cells of one precision overlap only when they are
the same cell, so that box is unique; `stationary_box` refuses rather than
picks when boxes off the firmware grid make two of them tie.

The ADR 0003 spike's `boxes_overlap` computes the same test in floating point
and counts some touching boxes as overlapping; it is not used here.
"""

from collections.abc import Collection
from itertools import combinations

PositionBox = tuple[int, int, int]
"""latitude_i, longitude_i (1e-7 degree integers, EPSG:4326) and precision_bits."""

MAX_BITS = 32


class TiedBoxesError(ValueError):
    """Overlapping boxes share the most precision bits but differ, so no smallest box exists."""


def _doubled_half_width(precision_bits: int) -> int:
    """Twice a box's half-width in 1e-7 degree units: 2^(32 - precision_bits).

    Raises:
        ValueError: if precision_bits is outside 0 to 32.
    """
    if not 0 <= precision_bits <= MAX_BITS:
        raise ValueError(f"precision_bits {precision_bits} is outside 0 to {MAX_BITS}")
    return 1 << (MAX_BITS - precision_bits)


def boxes_overlap(a: PositionBox, b: PositionBox) -> bool:
    """Whether two position boxes share ground; boxes that only touch do not.

    Args:
        a: latitude_i, longitude_i (1e-7 degrees, EPSG:4326) and precision_bits.
        b: The other box, in the same form.

    Returns:
        True when the centres differ by less than the sum of the half-widths
        on both axes.

    Raises:
        ValueError: if a precision is outside 0 to 32 bits.
    """
    reach = _doubled_half_width(a[2]) + _doubled_half_width(b[2])
    return 2 * abs(a[0] - b[0]) < reach and 2 * abs(a[1] - b[1]) < reach


def stationary_box[B: PositionBox](boxes: Collection[B]) -> B | None:
    """The smallest of a node's boxes when they all overlap one another, else None.

    Args:
        boxes: The node's distinct usable boxes, in the form `boxes_overlap` takes.

    Returns:
        The box with the most precision bits when there is at least one box and
        every two overlap; None when there is none or two do not overlap (the
        node moved).

    Raises:
        TiedBoxesError: if the boxes all overlap and two differ at the most bits.
        ValueError: if a precision is outside 0 to 32 bits.
    """
    for box in boxes:
        _doubled_half_width(box[2])
    if not boxes or not all(boxes_overlap(a, b) for a, b in combinations(boxes, 2)):
        return None
    most_bits = max(box[2] for box in boxes)
    smallest = [box for box in boxes if box[2] == most_bits]
    if len(smallest) > 1:
        raise TiedBoxesError(
            f"{len(smallest)} overlapping boxes share the most precision bits ({most_bits}); "
            "the rule places a node at one smallest box"
        )
    return smallest[0]
