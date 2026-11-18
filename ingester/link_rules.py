"""The contract's link-hour rules, `lnk_01` to `lnk_06`, over rows shaped like `raw.mqtt_message`.

`apply_link_rules` takes the rows of one snapshot, both brokers merged (Casey,
issue #55, Q1: "item 4 implements the contract as written on the merged
snapshot"), and gives every row's outcome and every (relayer, gateway, hour)
with a counted receipt. It follows the contract's order (`lnk_04`; Q9 (a);
D1), the steps the conformance fixture's README numbers:

1. `ing_06`: of several copies of one packet (origin, `packet_id`) at one
   gateway, the first by `received_at` is kept. `lnk_01`: a radio receipt
   (`via_mqtt` false, not the gateway's own packet, a non-zero `relay_node`)
   whose last relayer resolves to one node. A low byte of 0 is read as 0xFF.
   A relay byte equal to the sender's own is a direct hop, dropped and
   counted when the gateway's NeighborInfo names exactly one other node with
   that byte (Casey, PR #66, Q4 (a)). Otherwise the candidates are the
   senders of every radio delivery the gateway heard in the 24 hours before,
   its own included, and the gateway's own NeighborInfo reports in the same
   24 hours are counted together (Q5). Both window edges count (Q2).
2. `lnk_03`: relayer and gateway differ.
3. `lnk_06`: RSSI in [-150, 0) dBm and SNR in [-30, 20] dB, both present.
4. The positions a node reads: `precision_bits` 15 to 32, both coordinates,
   not 0, 0, stored value inside the MSA (`pos_01` to `pos_03`; Q3), either
   broker and either source.
5. Each end at each receipt: stationary on its 7-day look-back, or the
   position nearest in time within 30 minutes (`ingester.link_cells`).
6. Each end's box for the hour, over the receipts that passed 1 to 5, when it
   is stationary at all of them and their union is.
7. `lnk_05` per receipt, on the box each receipt uses.
8. One box per end over the counted receipts, or the hour is dropped and
   counted (`one_box_dropped_hours`, `mart_data_quality`'s count).
9. `lnk_04`: at least 3 counted receipts.
10. `lnk_05`: a median distance of at least 500 m.

The hour is `received_at` truncated to the hour in UTC, half-open (Q2).
`rx_time` is never read (`ing_06`). Each end's precise flag reads only the
positions `lnk_02` read for it in the hour, look-back included (`pos_04`;
Casey, D7 and PR #66, Q1 (c)).

`direct_hop_contradicted` is the count the fixture names
`lnk_01_direct_hop_contradicted` (Q4 (a)). It counts receipts, under three
readings ingest-eng proposed on PR #68: a receipt counts once, after
`ing_06` keeps the first copy; both bytes are compared after reading 0 as
0xFF; and a receipt counts when `lnk_01` drops it, whatever later rules
would have done.

Two readings the fixture does not pin, each a choice of this module: the
copies `ing_06` drops are left out as receipts only, so every position
delivery, including a later copy of one packet, counts for the precise flag;
and a NeighborInfo report is the gateway's own when the gateway sent it,
whichever gateway uplinked it. Rows with a null or zero `packet_id` are never
treated as copies of each other.
"""

import math
import statistics
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from ingester.link_cells import (
    Cell,
    LinkBox,
    all_same_place,
    box_around,
    box_distance_m,
    cell_of,
    cells_overlap,
)

Row = Mapping[str, Any]
InArea = Callable[[int, int], bool]

CANDIDATE_WINDOW = timedelta(hours=24)
LOOK_BACK = timedelta(days=7)
FALLBACK = timedelta(minutes=30)
MIN_DISTANCE_M = 500.0
MAX_DISTANCE_M = 60_000.0
MIN_PACKETS = 3
RSSI_MIN_DBM, RSSI_MAX_DBM = -150.0, 0.0
SNR_MIN_DB, SNR_MAX_DB = -30.0, 20.0
USABLE_BITS, PRECISE_BITS, MAX_BITS = 15, 20, 32
NEIGHBORINFO_APP = 71
BYTE_ZERO_READ_AS = 0xFF
COUNTED, DROPPED, KEPT = "counted", "dropped", "kept"
RELAY_NODE, NEIGHBOR_INFO = "relay_node", "neighbor_info"


@dataclass(frozen=True)
class ReceiptOutcome:
    """One row's outcome: `counted` when it passes step 7, else `dropped` with the rule.

    `relayer` and `link_source` are set once `lnk_01` resolves the relayer;
    the boxes and `distance_m` once the receipt reaches step 7.
    """

    delivery_key: str
    outcome: str
    rule: str | None = None
    relayer: str | None = None
    link_source: str | None = None
    relayer_box: LinkBox | None = None
    gateway_box: LinkBox | None = None
    distance_m: float | None = None


@dataclass(frozen=True)
class LinkHour:
    """A (relayer, gateway, hour) with at least one counted receipt.

    `outcome` is `kept`, or `dropped` with the rule of step 8 (`lnk_02`),
    9 (`lnk_04`) or 10 (`lnk_05`). The medians, distance, boxes, precise
    flags and `position_uncertainty_m` are set on a kept hour only.
    """

    relayer: str
    gateway: str
    hour: datetime
    receipts: tuple[str, ...]
    outcome: str
    rule: str | None = None
    median_snr_db: float | None = None
    median_rssi_dbm: float | None = None
    distance_m: float | None = None
    relayer_box: LinkBox | None = None
    relayer_precise_source: bool | None = None
    gateway_box: LinkBox | None = None
    gateway_precise_source: bool | None = None
    position_uncertainty_m: float | None = None

    @property
    def packets(self) -> int:
        return len(self.receipts)


@dataclass(frozen=True)
class LinkRulesResult:
    """Every row's outcome, every link-hour, and the two data-quality counts.

    Attributes:
        receipts: Per delivery key, its outcome.
        link_hours: Every (relayer, gateway, hour) with a counted receipt.
        direct_hop_contradicted: Receipts dropped because the gateway's
            NeighborInfo names another node with the sender's own byte
            (Casey, PR #66, Q4 (a)).
        one_box_dropped_hours: Hours dropped at step 8 (`lnk_02`; Q6 and Q9).
    """

    receipts: dict[str, ReceiptOutcome]
    link_hours: tuple[LinkHour, ...]
    direct_hop_contradicted: int
    one_box_dropped_hours: int


@dataclass(frozen=True)
class Position:
    """A position `lnk_02` reads, with whether its delivery qualifies under `pos_04`."""

    received_at: datetime
    cell: Cell
    qualifies: bool


class Timeline[T]:
    """Items in received_at order, read by inclusive time windows."""

    def __init__(self, entries: Iterable[tuple[datetime, T]]) -> None:
        ordered = sorted(entries, key=lambda entry: entry[0])
        self.times = [when for when, _ in ordered]
        self.items = [item for _, item in ordered]

    def between(self, start: datetime, end: datetime) -> list[T]:
        """The items from start to end, both included (Casey, PR #66, Q2)."""
        return self.items[bisect_left(self.times, start) : bisect_right(self.times, end)]


@dataclass(frozen=True)
class EndAt:
    """The box an end uses at a receipt or for an hour, and the positions read to choose it."""

    box: LinkBox
    covered: frozenset[Cell]
    read: tuple[Position, ...]
    stationary: bool


@dataclass
class Index:
    """The rows read by time: positions per node, radio senders and NeighborInfo per gateway."""

    positions: dict[str, Timeline[Position]] = field(default_factory=dict)
    heard: dict[tuple[str, int], Timeline[str]] = field(default_factory=dict)
    neighbours: dict[str, Timeline[tuple[tuple[str, int], ...]]] = field(default_factory=dict)


@dataclass(frozen=True)
class Staged:
    """A receipt that passed steps 1 to 5, with each end's own box."""

    row: Row
    relayer: str
    link_source: str
    relayer_end: EndAt
    gateway_end: EndAt

    @property
    def key(self) -> tuple[str, str, datetime]:
        return self.relayer, self.row["gateway_pseudonym"], hour_of(self.row["received_at"])


def hour_of(when: datetime) -> datetime:
    """The clock hour in UTC that holds a time, half-open: on the hour belongs to that hour."""
    return when.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def low_byte(value: int | None) -> int | None:
    """A node's low byte as `relay_node` names it: 0 is read as 0xFF."""
    if value is None:
        return None
    return BYTE_ZERO_READ_AS if value == 0 else int(value)


def apply_link_rules(rows: Sequence[Row], inside: InArea) -> LinkRulesResult:
    """Run the contract's link-hour rules over the rows of one snapshot.

    Args:
        rows: Rows shaped like `raw.mqtt_message`, with `received_at` a
            timezone-aware datetime and absent values None.
        inside: The MSA test on stored 1e-7 degree integers (`ingester.msa`).

    Returns:
        Every row's outcome, the link-hours and the data-quality counts.
    """
    ordered = sorted(rows, key=lambda row: (row["received_at"], row["delivery_key"]))
    index = build_index(ordered, inside)
    copies = later_copies(ordered)
    outcomes: dict[str, ReceiptOutcome] = {}
    staged: list[Staged] = []
    contradicted = 0
    for row in ordered:
        key = row["delivery_key"]
        if key in copies:
            outcomes[key] = ReceiptOutcome(key, DROPPED, "ing_06")
            continue
        found, flagged = per_receipt(row, index)
        contradicted += flagged
        if isinstance(found, Staged):
            staged.append(found)
        else:
            outcomes[key] = found
    hours = link_hours(staged, index, outcomes)
    one_box = sum(1 for hour in hours if hour.rule == "lnk_02")
    return LinkRulesResult(outcomes, tuple(hours), contradicted, one_box)


def read_position(row: Row, inside: InArea) -> Position | None:
    """The row's position when `lnk_02` reads it (`pos_01` to `pos_03`), else None."""
    latitude_i, longitude_i, bits = row["latitude_i"], row["longitude_i"], row["precision_bits"]
    if row["position_source"] is None or latitude_i is None or longitude_i is None:
        return None
    if bits is None or not USABLE_BITS <= bits <= MAX_BITS:
        return None
    if (latitude_i == 0 and longitude_i == 0) or not inside(latitude_i, longitude_i):
        return None
    qualifies = bits >= PRECISE_BITS and bool(row["seen_meshstl"])
    return Position(row["received_at"], Cell(latitude_i, longitude_i, bits), qualifies)


def build_index(rows: Sequence[Row], inside: InArea) -> Index:
    """Positions per node, radio senders per (gateway, byte) and NeighborInfo per gateway."""
    positions: dict[str, list[tuple[datetime, Position]]] = {}
    heard: dict[tuple[str, int], list[tuple[datetime, str]]] = {}
    neighbours: dict[str, list[tuple[datetime, tuple[tuple[str, int], ...]]]] = {}
    for row in rows:
        when, origin = row["received_at"], row["origin_pseudonym"]
        position = read_position(row, inside)
        if position is not None:
            positions.setdefault(origin, []).append((when, position))
        sender_byte = low_byte(row["origin_low_byte"])
        if row["via_mqtt"] is False and origin and sender_byte is not None:
            heard.setdefault((row["gateway_pseudonym"], sender_byte), []).append((when, origin))
        report = neighbour_report(row)
        if report is not None:
            neighbours.setdefault(origin, []).append((when, report))
    return Index(
        {node: Timeline(entries) for node, entries in positions.items()},
        {key: Timeline(entries) for key, entries in heard.items()},
        {node: Timeline(entries) for node, entries in neighbours.items()},
    )


def neighbour_report(row: Row) -> tuple[tuple[str, int], ...] | None:
    """A NeighborInfo row's (neighbour, byte) pairs, bytes of 0 read as 0xFF; else None."""
    names, low_bytes = row["neighbour_pseudonyms"], row["neighbour_low_bytes"]
    if row["portnum"] != NEIGHBORINFO_APP or names is None or low_bytes is None:
        return None
    return tuple(
        (name, read)
        for name, value in zip(names, low_bytes, strict=True)
        if (read := low_byte(value)) is not None
    )


def later_copies(rows: Sequence[Row]) -> set[str]:
    """Delivery keys of every copy of a packet at a gateway after the first by received_at.

    Args:
        rows: The rows in (received_at, delivery_key) order.
    """
    first: set[tuple[str, int, str]] = set()
    later: set[str] = set()
    for row in rows:
        if not row["packet_id"]:
            continue
        packet = (row["origin_pseudonym"], row["packet_id"], row["gateway_pseudonym"])
        if packet in first:
            later.add(row["delivery_key"])
        first.add(packet)
    return later


def per_receipt(row: Row, index: Index) -> tuple[ReceiptOutcome | Staged, bool]:
    """Steps 1 to 5 for one row: its drop, or the receipt with each end's own box.

    Returns:
        The outcome or the staged receipt, and whether it is a direct hop
        that NeighborInfo contradicts (Q4).
    """
    key = row["delivery_key"]
    if not radio_receipt(row):
        return ReceiptOutcome(key, DROPPED, "lnk_01"), False
    relayer, source, contradicted = resolve(row, index)
    if relayer is None or source is None:
        return ReceiptOutcome(key, DROPPED, "lnk_01"), contradicted
    gateway = row["gateway_pseudonym"]
    if relayer == gateway:
        return ReceiptOutcome(key, DROPPED, "lnk_03", relayer, source), False
    if not signal_in_range(row):
        return ReceiptOutcome(key, DROPPED, "lnk_06", relayer, source), False
    when = row["received_at"]
    relayer_end, gateway_end = end_at(index, relayer, when), end_at(index, gateway, when)
    if relayer_end is None or gateway_end is None:
        return ReceiptOutcome(key, DROPPED, "lnk_02", relayer, source), False
    return Staged(row, relayer, source, relayer_end, gateway_end), False


def radio_receipt(row: Row) -> bool:
    """`via_mqtt` false, not the gateway's own packet, and a non-zero `relay_node` (`lnk_01`)."""
    own = row["origin_pseudonym"] == row["gateway_pseudonym"]
    return row["via_mqtt"] is False and not own and bool(row["relay_node"])


def resolve(row: Row, index: Index) -> tuple[str | None, str | None, bool]:
    """The relayer and `link_source` under `lnk_01`, and whether Q4 dropped a direct hop."""
    relay, when = int(row["relay_node"]), row["received_at"]
    gateway, origin = row["gateway_pseudonym"], row["origin_pseudonym"]
    neighbour = only_one(
        name
        for report in within_day(index.neighbours.get(gateway), when)
        for name, read in report
        if read == relay
    )
    if relay == low_byte(row["origin_low_byte"]):
        if neighbour is not None and neighbour != origin:
            return None, None, True
        return origin, RELAY_NODE, False
    candidate = only_one(within_day(index.heard.get((gateway, relay)), when))
    if candidate is not None and neighbour is not None and candidate != neighbour:
        return None, None, False
    if candidate is not None:
        return candidate, RELAY_NODE, False
    if neighbour is not None:
        return neighbour, NEIGHBOR_INFO, False
    return None, None, False


def within_day[T](timeline: Timeline[T] | None, when: datetime) -> list[T]:
    """The items in the 24 hours before a time, both edges included (Q2)."""
    return [] if timeline is None else timeline.between(when - CANDIDATE_WINDOW, when)


def only_one(names: Iterable[str]) -> str | None:
    """The one distinct name, or None when there is none or more than one."""
    distinct = set(names)
    return next(iter(distinct)) if len(distinct) == 1 else None


def present(value: Any) -> bool:
    """A reading that is there and is a number; NaN is missing (`lnk_06`)."""
    return value is not None and not math.isnan(float(value))


def signal_in_range(row: Row) -> bool:
    """`lnk_06`: RSSI in [-150, 0) dBm and SNR in [-30, 20] dB, both present."""
    rssi, snr = row["rx_rssi"], row["rx_snr"]
    if not (present(rssi) and present(snr)):
        return False
    return RSSI_MIN_DBM <= float(rssi) < RSSI_MAX_DBM and SNR_MIN_DB <= float(snr) <= SNR_MAX_DB


def end_at(index: Index, node: str, when: datetime) -> EndAt | None:
    """An end's own box at a receipt: stationary on its 7-day look-back, else the fallback.

    The fallback takes the position nearest in time within 30 minutes either
    side, the earlier on a tie; positions at one instant keep row order.
    """
    timeline = index.positions.get(node)
    if timeline is None:
        return None
    look_back = timeline.between(when - LOOK_BACK, when)
    cells = frozenset(position.cell for position in look_back)
    if look_back and all_same_place(cells):
        box, covered = box_around(cells)
        return EndAt(box, covered, tuple(look_back), stationary=True)
    near = timeline.between(when - FALLBACK, when + FALLBACK)
    if not near:
        return None
    chosen = min(near, key=lambda p: (abs(p.received_at - when), p.received_at))
    read = tuple(dict.fromkeys([*look_back, *near]))
    return EndAt(cell_of(*chosen.cell), frozenset({chosen.cell}), read, stationary=False)


def hour_end(
    index: Index, node: str, ends: Sequence[EndAt], first: datetime, last: datetime
) -> EndAt | None:
    """The end's box for the hour (step 6), or None when each receipt keeps its own.

    Args:
        index: The rows read by time.
        node: The end's pseudonym.
        ends: Its own box at each of the hour's receipts that passed steps 1 to 5.
        first: The first of those receipts' received_at.
        last: The last of them.
    """
    if not all(end.stationary for end in ends):
        return None
    union = index.positions[node].between(first - LOOK_BACK, last)
    cells = frozenset(position.cell for position in union)
    if not all_same_place(cells):
        return None
    box, covered = box_around(cells)
    return EndAt(box, covered, tuple(union), stationary=True)


def link_hours(
    staged: Sequence[Staged], index: Index, outcomes: dict[str, ReceiptOutcome]
) -> list[LinkHour]:
    """Steps 6 to 10 for every (relayer, gateway, hour); fills outcomes for step 7."""
    groups: dict[tuple[str, str, datetime], list[Staged]] = {}
    for receipt in staged:
        groups.setdefault(receipt.key, []).append(receipt)
    hours = []
    for (relayer, gateway, hour), group in groups.items():
        first, last = group[0].row["received_at"], group[-1].row["received_at"]
        r_hour = hour_end(index, relayer, [s.relayer_end for s in group], first, last)
        g_hour = hour_end(index, gateway, [s.gateway_end for s in group], first, last)
        used = [
            (
                s,
                s.relayer_end if r_hour is None else r_hour,
                s.gateway_end if g_hour is None else g_hour,
            )
            for s in group
        ]
        counted = [entry for entry in used if passes_distance(entry, outcomes)]
        if counted:
            ends = (r_hour, g_hour)
            hours.append(close_hour((relayer, gateway, hour), counted, ends))
    return hours


Used = tuple[Staged, EndAt, EndAt]
"""A receipt with the relayer's and the gateway's box it uses."""


def passes_distance(entry: Used, outcomes: dict[str, ReceiptOutcome]) -> bool:
    """Step 7, `lnk_05` per receipt, recording the receipt's outcome."""
    receipt, relayer_end, gateway_end = entry
    distance_m = box_distance_m(relayer_end.box, gateway_end.box)
    in_range = MIN_DISTANCE_M <= distance_m <= MAX_DISTANCE_M
    passes = in_range and not cells_overlap(relayer_end.box, gateway_end.box)
    key = receipt.row["delivery_key"]
    outcomes[key] = ReceiptOutcome(
        key, COUNTED if passes else DROPPED, None if passes else "lnk_05", receipt.relayer,
        receipt.link_source, relayer_end.box, gateway_end.box, distance_m,
    )  # fmt: skip
    return passes


def close_hour(
    key: tuple[str, str, datetime], counted: Sequence[Used], hour_ends: tuple[EndAt | None, ...]
) -> LinkHour:
    """Steps 8 to 10 over the counted receipts, and a kept hour's values."""
    receipts = tuple(entry[0].row["delivery_key"] for entry in counted)
    relayer_boxes = {entry[1].box.edges for entry in counted}
    gateway_boxes = {entry[2].box.edges for entry in counted}
    if len(relayer_boxes) > 1 or len(gateway_boxes) > 1:
        return LinkHour(*key, receipts, DROPPED, "lnk_02")
    if len(counted) < MIN_PACKETS:
        return LinkHour(*key, receipts, DROPPED, "lnk_04")
    distance_m = statistics.median(box_distance_m(entry[1].box, entry[2].box) for entry in counted)
    if distance_m < MIN_DISTANCE_M:
        return LinkHour(*key, receipts, DROPPED, "lnk_05")
    relayer_box, gateway_box = counted[0][1].box, counted[0][2].box
    return LinkHour(
        *key, receipts, KEPT, None,
        float(statistics.median(float(e[0].row["rx_snr"]) for e in counted)),
        float(statistics.median(float(e[0].row["rx_rssi"]) for e in counted)),
        float(distance_m), relayer_box, precise_source([e[1] for e in counted], hour_ends[0]),
        gateway_box, precise_source([e[2] for e in counted], hour_ends[1]),
        relayer_box.larger_half_width_m + gateway_box.larger_half_width_m,
    )  # fmt: skip


def precise_source(used: Sequence[EndAt], hour: EndAt | None) -> bool:
    """`pos_04` for an end of a kept hour (Casey, D7; PR #66, Q1 (c)).

    True when each cell the box covers was sent in a qualifying delivery
    among the positions `lnk_02` read for that end in the hour: for an end
    on the hour's box, the union that box was chosen from; otherwise the
    union of the positions each counted receipt read, look-back included.
    """
    ends = [hour] if hour is not None else used
    covered = frozenset(cell for end in ends for cell in end.covered)
    read = [position for end in ends for position in end.read]
    qualifying = {position.cell for position in read if position.qualifies}
    return covered <= qualifying
