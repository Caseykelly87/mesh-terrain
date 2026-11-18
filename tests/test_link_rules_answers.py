"""Casey's answers Q1 to Q5 on PR #66 (comment 6968763406, item 2), on synthetic rows.

The follow-up fixture cases for these answers (B2b) are warehouse-eng's; these
tests pin the same answers in the ingester until those cases are on main:

- Q1 (c): an end on the 30-minute fallback is a precise source when its cell
  was sent in a qualifying delivery among exactly the positions `lnk_02` read
  for that end, the 7-day look-back included, and nothing more.
- Q2: both edges of the 24-hour and 7-day windows count; the clock hour of a
  link-hour stays half-open.
- Q3: `pos_02` tests a 32-bit position's stored value.
- Q4 (a): a direct hop that the gateway's NeighborInfo contradicts is dropped
  and counted.
- Q5: "exactly one neighbour with that low byte" counts across all of the
  gateway's reports in the 24 hours together.

Nodes and positions are invented: the gateway's 16-bit cell and the relayer's
20-bit cell are the fixture's g16 and r20, 2745.603 m apart inside the MSA.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ingester.link_cells import Cell, cell_of, cells_overlap, same_place
from ingester.link_rules import LinkRulesResult, apply_link_rules
from ingester.msa import msa_contains

GATEWAY_CELL = (385974272, -903970816, 16)
RELAYER_CELL = (386000896, -903657472, 20)
# A 20-bit cell about 1 km north of RELAYER_CELL: not the same place.
MOVED_CELL = (386090496, -903657472, 20)
G, R, S, N1, N2 = "t-g", "t-r", "t-s", "t-n1", "t-n2"
# X shares R's low byte; T shares the byte of N1 and N2.
X, T = "t-x", "t-t"
BYTES = {G: 10, R: 20, S: 30, N1: 40, N2: 40, X: 20, T: 40}
RECEIPT_MINUTES = (10, 20, 30)


def at(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, second, tzinfo=UTC)


class Rows:
    """Raw-shaped rows with unique delivery keys and packet ids."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def add(self, origin: str, gateway: str, when: datetime, **fields: Any) -> str:
        key = f"t-m{len(self.rows) + 1:03d}"
        row: dict[str, Any] = {
            "delivery_key": key,
            "received_at": when,
            "origin_pseudonym": origin,
            "origin_low_byte": BYTES[origin],
            "gateway_pseudonym": gateway,
            "gateway_low_byte": BYTES[gateway],
            "via_mqtt": False,
            "seen_public": True,
            "seen_meshstl": False,
            "packet_id": 9000 + len(self.rows),
            "relay_node": None,
            "portnum": 67,
            "rx_snr": None,
            "rx_rssi": None,
            "position_source": None,
            "latitude_i": None,
            "longitude_i": None,
            "precision_bits": None,
            "neighbour_pseudonyms": None,
            "neighbour_low_bytes": None,
        }
        row.update(fields)
        self.rows.append(row)
        return key

    def position(
        self, node: str, when: datetime, cell: tuple[int, int, int], meshstl: bool = False
    ) -> str:
        latitude_i, longitude_i, bits = cell
        return self.add(
            node, node, when, portnum=3, position_source="position", latitude_i=latitude_i,
            longitude_i=longitude_i, precision_bits=bits, seen_public=not meshstl,
            seen_meshstl=meshstl,
        )  # fmt: skip

    def receipt(self, origin: str, when: datetime, relay: int) -> str:
        return self.add(origin, G, when, relay_node=relay, portnum=1, rx_snr=5.0, rx_rssi=-90)

    def neighbours(self, when: datetime, names: list[str]) -> str:
        return self.add(
            G, G, when, portnum=71, neighbour_pseudonyms=names,
            neighbour_low_bytes=[BYTES[name] for name in names],
        )  # fmt: skip

    def placed(self, *nodes: str) -> None:
        """Each node's own cell the day before, so both ends are stationary."""
        for node in nodes:
            self.position(node, at(22, 9), RELAYER_CELL if node != G else GATEWAY_CELL)

    def run(self) -> LinkRulesResult:
        return apply_link_rules(self.rows, msa_contains())


def test_q4_a_direct_hop_contradicted_by_neighbor_info_is_dropped_and_counted() -> None:
    rows = Rows()
    rows.placed(G, R)
    rows.neighbours(at(23, 8), [X])
    keys = [rows.receipt(R, at(23, 10, m), BYTES[R]) for m in RECEIPT_MINUTES]
    result = rows.run()
    assert [result.receipts[key].outcome for key in keys] == ["dropped"] * 3
    assert {result.receipts[key].rule for key in keys} == {"lnk_01"}
    assert result.direct_hop_contradicted == 3


def test_q4_direct_hop_kept_when_neighbor_info_names_the_sender_or_does_not_resolve() -> None:
    rows = Rows()
    rows.placed(G, R)
    rows.neighbours(at(23, 8), [R, N1, N2])
    agree = rows.receipt(R, at(23, 10, 10), BYTES[R])
    rows.position(T, at(22, 9), RELAYER_CELL)
    # T's direct hop: N1 and N2 share T's byte, so NeighborInfo does not resolve it.
    unresolved = rows.receipt(T, at(23, 10, 20), BYTES[T])
    result = rows.run()
    assert result.receipts[agree].outcome == "counted"
    assert result.receipts[agree].link_source == "relay_node"
    assert result.receipts[unresolved].relayer == T
    assert result.direct_hop_contradicted == 0


def test_q5_neighbours_count_across_every_report_in_the_24_hours() -> None:
    rows = Rows()
    rows.placed(G)
    rows.position(N1, at(22, 9), RELAYER_CELL)
    rows.neighbours(at(23, 6), [N1])
    rows.neighbours(at(23, 8), [N2])
    across = rows.receipt(S, at(23, 10, 10), 40)
    result = rows.run()
    assert result.receipts[across].outcome == "dropped"
    assert result.receipts[across].rule == "lnk_01"


def test_q5_one_neighbour_in_two_reports_is_one_neighbour() -> None:
    rows = Rows()
    rows.placed(G)
    rows.position(N1, at(22, 9), RELAYER_CELL)
    rows.neighbours(at(23, 6), [N1])
    rows.neighbours(at(23, 8), [N1])
    key = rows.receipt(S, at(23, 10, 10), 40)
    found = rows.run().receipts[key]
    assert (found.relayer, found.link_source) == (N1, "neighbor_info")


@pytest.mark.parametrize(
    ("before", "resolves"),
    [(timedelta(hours=24), True), (timedelta(hours=24, seconds=1), False)],
)
def test_q2_a_candidate_exactly_24_hours_before_counts(before: timedelta, resolves: bool) -> None:
    rows = Rows()
    rows.placed(G, R)
    receipt_at = at(23, 10, 10)
    rows.add(R, G, receipt_at - before)
    key = rows.receipt(S, receipt_at, BYTES[R])
    assert (rows.run().receipts[key].relayer == R) is resolves


@pytest.mark.parametrize(
    ("before", "resolves"),
    [(timedelta(hours=24), True), (timedelta(hours=24, seconds=1), False)],
)
def test_q2_neighbor_info_exactly_24_hours_before_counts(before: timedelta, resolves: bool) -> None:
    rows = Rows()
    rows.placed(G, N1)
    receipt_at = at(23, 10, 10)
    rows.neighbours(receipt_at - before, [N1])
    key = rows.receipt(S, receipt_at, 40)
    assert (rows.run().receipts[key].relayer == N1) is resolves


def test_q2_a_delivery_at_the_receipts_own_instant_is_before_it() -> None:
    rows = Rows()
    rows.placed(G)
    receipt_at = at(23, 10, 10)
    # R's only position arrives at the receipt's instant: read by the 7-day look-back.
    rows.position(R, receipt_at, RELAYER_CELL)
    rows.add(R, G, receipt_at)
    key = rows.receipt(S, receipt_at, BYTES[R])
    found = rows.run().receipts[key]
    assert found.relayer == R
    assert found.outcome == "counted"


def test_q2_the_clock_hour_is_half_open() -> None:
    rows = Rows()
    rows.placed(G, R)
    keys = [rows.receipt(R, when, BYTES[R]) for when in (at(23, 11), at(23, 11, 1), at(23, 11, 2))]
    rows.receipt(R, at(23, 10, 59, 59), BYTES[R])
    hours = {hour.hour: hour for hour in rows.run().link_hours}
    assert sorted(hours[at(23, 11)].receipts) == keys
    assert hours[at(23, 10)].packets == 1


def fallback_rows(meshstl_copy_at: datetime | None) -> Rows:
    """R moved (two cells far apart in its look-back) and takes RELAYER_CELL from the fallback."""
    rows = Rows()
    rows.placed(G)
    rows.position(R, at(22, 9), MOVED_CELL)
    rows.position(R, at(23, 10, 5), RELAYER_CELL)
    if meshstl_copy_at is not None:
        rows.position(R, meshstl_copy_at, RELAYER_CELL, meshstl=True)
    for minute in RECEIPT_MINUTES:
        rows.receipt(R, at(23, 10, minute), BYTES[R])
    return rows


@pytest.mark.parametrize(
    ("meshstl_copy_at", "precise"),
    [
        (at(21, 10), True),  # two days before: in the 7-day look-back
        (at(16, 10, 9), False),  # outside every window lnk_02 read
        (at(23, 11, 5), False),  # 35 minutes after the last receipt
        (None, False),
    ],
)
def test_q1_c_fallback_flag_reads_the_look_back_and_nothing_more(
    meshstl_copy_at: datetime | None, precise: bool
) -> None:
    result = fallback_rows(meshstl_copy_at).run()
    (hour,) = [hour for hour in result.link_hours if hour.relayer == R]
    assert hour.outcome == "kept"
    assert hour.relayer_box is not None and hour.relayer_box.precision_bits == RELAYER_CELL[2]
    assert hour.relayer_precise_source is precise


def test_q3_the_msa_test_reads_a_positions_stored_value() -> None:
    rows = Rows()
    rows.placed(G, R)
    asked: list[tuple[int, int]] = []

    def inside(latitude_i: int, longitude_i: int) -> bool:
        asked.append((latitude_i, longitude_i))
        return True

    rows.position(R, at(22, 10), (386000001, -903657001, 32))
    apply_link_rules(rows.rows, inside)
    assert (386000001, -903657001) in asked


def test_a_cell_is_half_open_from_the_firmware_low_edge() -> None:
    # -5 at width 4 (30 bits) gives low -8, stored -6 and the cell [-8, -4) (ADR 0003).
    cell = cell_of(-6, -6, 30)
    assert (cell.south_i, cell.north_i, cell.west_i, cell.east_i) == (-8, -4, -8, -4)
    full = cell_of(386000001, -903657001, 32)
    assert (full.south_i, full.north_i) == (386000001, 386000002)


def test_cells_that_only_touch_do_not_overlap() -> None:
    west = cell_of(386000000 + (1 << 15), -903610368 - (1 << 15), 16)
    east = cell_of(386000000 + (1 << 15), -903610368 + (1 << 15), 16)
    assert not cells_overlap(west, east)
    assert cells_overlap(west, west)


def test_the_floor_is_inclusive_and_pairwise() -> None:
    here = Cell(386000000, -903650000, 32)
    near = Cell(386000000, -903650100, 32)
    assert same_place(here, near)
    assert not same_place(here, Cell(386010000, -903650000, 32))
