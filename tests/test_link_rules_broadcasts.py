"""A broadcast's gateway copies read as one position (ADR 0003).

Casey's answer (issue #55 comment 6995037213, item 3): "A broadcast's gateway
copies count as one position, timed by the first copy, precise if any copy
qualifies." The link rules match copies by node, `packet_id` and stored values
(`latitude_i`, `longitude_i`, `precision_bits`), as warehouse-eng's
`int_usable_position` does on PR #72.
Fixture cases c52 to c55 pin the windows; these tests pin the grouping itself,
including the edges no case holds: a copy without a `packet_id`, copies that
differ in stored values, and two nodes that share a `packet_id`.

Nodes and positions are invented; the cell is the fixture's 20-bit c20, inside
the MSA.
"""

from datetime import UTC, datetime
from typing import Any

from ingester.link_cells import Cell
from ingester.link_rules import Position, build_index
from ingester.msa import msa_contains

CELL = (386000896, -903657472, 20)
# The same place sent at 16 bits: other stored values, so another position.
COARSER = (385974272, -903643136, 16)
NODE, OTHER = "b-r", "b-s"


def at(minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 23, 10, minute, second, tzinfo=UTC)


def copy(
    key: str,
    when: datetime,
    gateway: str,
    *,
    meshstl: bool = False,
    packet_id: int | None = 7001,
    node: str = NODE,
    cell: tuple[int, int, int] = CELL,
) -> dict[str, Any]:
    """One gateway's copy of a Position broadcast, shaped like a `raw.mqtt_message` row."""
    latitude_i, longitude_i, bits = cell
    return {
        "delivery_key": key,
        "received_at": when,
        "origin_pseudonym": node,
        "origin_low_byte": 21,
        "gateway_pseudonym": gateway,
        "gateway_low_byte": 31,
        "via_mqtt": False,
        "seen_public": not meshstl,
        "seen_meshstl": meshstl,
        "packet_id": packet_id,
        "relay_node": None,
        "portnum": 3,
        "position_source": "position",
        "latitude_i": latitude_i,
        "longitude_i": longitude_i,
        "precision_bits": bits,
        "neighbour_pseudonyms": None,
        "neighbour_low_bytes": None,
    }


def positions(*rows: dict[str, Any], node: str = NODE) -> list[Position]:
    return build_index(rows, msa_contains()).positions[node].items


def test_copies_are_one_position_timed_by_the_first_and_precise_through_a_later_copy() -> None:
    found = positions(copy("b-1", at(40), "b-h1"), copy("b-2", at(40, 1), "b-h2", meshstl=True))
    assert found == [Position(at(40), Cell(*CELL), qualifies=True)]


def test_the_first_copy_times_the_broadcast_whatever_the_row_order() -> None:
    found = positions(copy("b-2", at(41), "b-h2"), copy("b-1", at(40), "b-h1", meshstl=True))
    assert found == [Position(at(40), Cell(*CELL), qualifies=True)]


def test_a_broadcast_with_no_qualifying_copy_is_not_precise() -> None:
    found = positions(copy("b-1", at(40), "b-h1"), copy("b-2", at(40, 1), "b-h2"))
    assert found == [Position(at(40), Cell(*CELL), qualifies=False)]


def test_a_repeat_at_one_gateway_is_a_copy_of_the_broadcast() -> None:
    found = positions(copy("b-1", at(40), "b-h1"), copy("b-2", at(45), "b-h1", meshstl=True))
    assert found == [Position(at(40), Cell(*CELL), qualifies=True)]


def test_copies_without_a_packet_id_are_each_their_own_position() -> None:
    for packet_id in (None, 0):
        found = positions(
            copy("b-1", at(40), "b-h1", packet_id=packet_id),
            copy("b-2", at(40, 1), "b-h2", meshstl=True, packet_id=packet_id),
        )
        assert found == [
            Position(at(40), Cell(*CELL), qualifies=False),
            Position(at(40, 1), Cell(*CELL), qualifies=True),
        ], f"packet_id {packet_id}"


def test_copies_that_differ_in_stored_values_are_separate_positions() -> None:
    found = positions(
        copy("b-1", at(40), "b-h1"), copy("b-2", at(40, 1), "b-h2", cell=COARSER, meshstl=True)
    )
    assert found == [
        Position(at(40), Cell(*CELL), qualifies=False),
        Position(at(40, 1), Cell(*COARSER), qualifies=False),
    ]


def test_two_nodes_that_share_a_packet_id_send_separate_positions() -> None:
    rows = (copy("b-1", at(40), "b-h1"), copy("b-2", at(40, 1), "b-h2", node=OTHER, meshstl=True))
    assert positions(*rows) == [Position(at(40), Cell(*CELL), qualifies=False)]
    assert positions(*rows, node=OTHER) == [Position(at(40, 1), Cell(*CELL), qualifies=True)]
