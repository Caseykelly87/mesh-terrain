"""The pairs positioned at both ends in a capture snapshot, by the ADR 0003 spike's own rules.

A pair is a (relayer, gateway) link with at least one link-hour in the MSA
under the stationarity rule, in the public broker's whole capture or the
MeshSTL broker's 24 hours: relayer resolved (`lnk_01`), both ends positioned
(`lnk_02`), different nodes (`lnk_03`), at least 3 receipts in the clock hour
(`lnk_04`), 500 m to 60 km between box centres whose boxes do not overlap
(`lnk_05`), signal in range (`lnk_06`, at the spike's limits; ADR 0003 records
that the contract's wider ones change no link-hour). That is the set the
reproduction gate counts (`docs/adr/0003-spike/reproduce_from_snapshot.py`,
`stationary_pairs`, 8 on the real snapshot): this module runs the spike's
`analyze.py` on the snapshot rows rather than porting it, so the set is the
spike's by construction. As in the spike, relayer candidates and the
stationarity test see the whole run, not the trailing 24 hours and 7 days the
warehouse applies (ADR 0003, Terms).

Where a node is placed follows the amended `lnk_02` (ADR 0003, Overlap
amendment, 2026-11-17), not the spike: `node_positions` reads only a node's
usable positions (15 bits or more) inside the MSA polygon, from either broker
and either source, over the whole capture, which stands in for the 7-day
window. The node is stationary when all of them overlap one another, tested
in whole numbers (`ingester.box_overlap`), and is placed at its smallest box.
The pairs themselves still come from the spike's rules, which test
stationarity per run with one box per node and overlap in floating point;
moving those rules into tested ingester code is a later unit (Casey, PR #52,
4).

What a position is follows the contract, not the gate: a row needs both
coordinates (`pos_01`), and one at 0, 0 is none (`pos_03`). The gate puts a
missing coordinate back as 0 to match the spike's archive reading (Casey,
2026-11-14); the count of rows where the two differ is reported, so a reader
sees whether they could give different pairs.

Every file read is listed in the snapshot's manifest and matches its sha256
(`snp_01`). The spike scripts are imported when the pairs are computed, not
at import, so the rest of the ingester and the Dagster image do not need
`docs/`. Nothing here prints or logs a coordinate.
"""

import importlib
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, NamedTuple

import pyarrow.parquet as pq
import structlog

from ingester.box_overlap import TiedBoxesError, stationary_box
from ingester.topics import topic_class, topic_root
from pipeline.common.snapshot import verified_manifest

log = structlog.get_logger(component="ingester.link_pairs")

REPO_ROOT = Path(__file__).resolve().parents[1]
SPIKE_DIR = REPO_ROOT / "docs" / "adr" / "0003-spike"
SPIKE_SCRIPTS = ("analyze.py", "figures.py")
DELIVERY_FOLDER = "mqtt_message/"
COLUMNS = [
    "received_at", "topic", "decode_status", "gateway_pseudonym", "gateway_low_byte",
    "origin_pseudonym", "origin_low_byte", "packet_id", "relay_node", "hop_start", "hop_limit",
    "via_mqtt", "rx_snr", "rx_rssi", "portnum", "position_source", "latitude_i", "longitude_i",
    "precision_bits", "neighbour_pseudonyms", "neighbour_low_bytes", "neighbour_snrs",
    "seen_public", "seen_meshstl",
]  # fmt: skip
NEIGHBORINFO_APP = 71
USABLE_BITS = 15
PRECISE_BITS = 20
# The two spike runs whose pairs make the union (docs/measurements-2026-11-08.md).
RUNS = {"public": "public_full", "meshstl": "meshstl_24h"}
MSA_GEOID = "41180"


class SelectionError(Exception):
    """Engine inputs are refused: the snapshot, spike scripts or output folder do not allow them."""


class Box(NamedTuple):
    """A reported position box: 1e-7 degree integers (EPSG:4326) and the bits kept."""

    latitude_i: int
    longitude_i: int
    precision_bits: int


InArea = Callable[[int, int], bool]
"""Whether latitude_i and longitude_i (1e-7 degrees, EPSG:4326) lie inside an area."""


@dataclass(frozen=True)
class NodePositions:
    """A node's distinct usable boxes inside the MSA over the capture, either broker or source.

    Attributes:
        boxes: At least one box, each of 15 bits or more (lnk_02).
        precise_source: Whether one of them is 20 bits or more on a MeshSTL
            delivery (`pos_04`).

    Raises:
        ValueError: if boxes is empty or holds a box under 15 bits.
    """

    boxes: frozenset[Box]
    precise_source: bool

    def __post_init__(self) -> None:
        if not self.boxes or any(box.precision_bits < USABLE_BITS for box in self.boxes):
            raise ValueError(f"a node needs one or more usable boxes, {USABLE_BITS} bits or more")

    @property
    def stationary_box(self) -> Box | None:
        """The node's smallest box when all its boxes overlap one another, else None (lnk_02).

        Raises:
            SelectionError: if overlapping boxes tie for the most precision
                bits, which firmware boxes cannot; the message names no
                coordinate.
        """
        try:
            return stationary_box(self.boxes)
        except TiedBoxesError as err:
            raise SelectionError(str(err)) from err


@dataclass(frozen=True)
class PairActivity:
    """One pair positioned at both ends, with the measures its busyness can be ranked by.

    Attributes:
        relayer: The relayer's pseudonym.
        gateway: The gateway's pseudonym.
        link_hours_public: Link-hours in the public run.
        link_hours_meshstl: Link-hours in the MeshSTL run.
        link_hours_union: Distinct link-hours over both runs; one both carried counts once.
        receipts: Receipts in those link-hours, public run plus MeshSTL run.
    """

    relayer: str
    gateway: str
    link_hours_public: int
    link_hours_meshstl: int
    link_hours_union: int
    receipts: int


@dataclass(frozen=True)
class CaptureLinks:
    """The pairs of a capture and the positions of every node that sent one."""

    pairs: tuple[PairActivity, ...]
    pair_counts: dict[str, int]
    nodes: dict[str, NodePositions]
    rows: int
    one_coordinate_missing: int


def read_capture_rows(folder: Path) -> list[dict[str, Any]]:
    """The delivery rows of a finished capture snapshot, after checking it against its manifest.

    Args:
        folder: The finished snapshot folder.

    Returns:
        One dict per stored delivery, with the columns the pair rules read.

    Raises:
        SnapshotError: if the folder has no manifest or a file differs from it.
    """
    started = time.monotonic()
    manifest = verified_manifest(folder)
    paths = sorted(
        entry["path"]
        for entry in manifest["files"]
        if entry["path"].startswith(DELIVERY_FOLDER) and entry["path"].endswith(".parquet")
    )
    rows: list[dict[str, Any]] = []
    for path in paths:
        rows += pq.read_table(folder / path, columns=COLUMNS).to_pylist()
    duration_s = round(time.monotonic() - started, 3)
    log.info(
        "capture_read", path=str(folder), files=len(paths), rows=len(rows), duration_s=duration_s
    )
    return rows


def position_of(row: dict[str, Any]) -> Box | None:
    """The row's position box, or None when it carries no position under pos_01 and pos_03."""
    latitude_i, longitude_i = row["latitude_i"], row["longitude_i"]
    if row["position_source"] is None or latitude_i is None or longitude_i is None:
        return None
    if latitude_i == 0 and longitude_i == 0:
        return None
    return Box(latitude_i, longitude_i, row["precision_bits"] or 0)


def capture_links(rows: Sequence[dict[str, Any]]) -> CaptureLinks:
    """The union of the spike's both-ends pairs in a capture, and every node's positions.

    Args:
        rows: Delivery rows from read_capture_rows.

    Returns:
        The pairs in a stable order, the counts per run, and the positions.

    Raises:
        SelectionError: if the spike scripts are not in the checkout.
    """
    figures = _spike()
    msa = _msa(figures)
    ids = StandIns()
    keys = {broker: _stationary_link_hours(figures, rows, ids, broker, msa) for broker in RUNS}
    pairs = _pair_activity(keys, ids)
    public, meshstl = ({(k[0], k[1]) for k in keys[b]} for b in RUNS)
    counts = {
        "public": len(public),
        "meshstl": len(meshstl),
        "shared": len(public & meshstl),
        "union": len(public | meshstl),
    }
    log.info("pairs_found", **counts)
    missing = sum(
        1
        for row in rows
        if row["position_source"] is not None
        and (row["latitude_i"] is None) != (row["longitude_i"] is None)
    )
    return CaptureLinks(pairs, counts, node_positions(rows, msa.contains), len(rows), missing)


def node_positions(rows: Iterable[dict[str, Any]], inside: InArea) -> dict[str, NodePositions]:
    """Every node's usable boxes in the area over the whole capture (lnk_02).

    Args:
        rows: Delivery rows from read_capture_rows, from either broker.
        inside: The area test, `msa_contains()` for the MSA.

    Returns:
        Per node with at least one usable box in the area, its boxes and
        whether one is a precise source (`pos_04`). Coarse boxes and boxes
        outside the area are not read, so a node with no other box is absent.
    """
    boxes: dict[str, set[Box]] = {}
    precise: set[str] = set()
    for row in rows:
        box = position_of(row)
        if box is None or box.precision_bits < USABLE_BITS:
            continue
        if not inside(box.latitude_i, box.longitude_i):
            continue
        node = row["origin_pseudonym"]
        boxes.setdefault(node, set()).add(box)
        if box.precision_bits >= PRECISE_BITS and row["seen_meshstl"]:
            precise.add(node)
    return {node: NodePositions(frozenset(found), node in precise) for node, found in boxes.items()}


@dataclass
class StandIns:
    """A stand-in integer per pseudonym with the stored low byte as its low byte, and back.

    analyze.py resolves relay_node by low byte on integer node ids, so this is
    the gate's mapping (reproduce_from_snapshot.StandIns) with the reverse kept.
    """

    ids: dict[str, int] = field(default_factory=dict)
    names: dict[int, str] = field(default_factory=dict)

    def of(self, pseudonym: str | None, low_byte: int | None) -> int:
        if not pseudonym:
            return 0
        if pseudonym not in self.ids:
            stand_in = ((len(self.ids) + 1) << 8) | int(low_byte or 0)
            self.ids[pseudonym] = stand_in
            self.names[stand_in] = pseudonym
        return self.ids[pseudonym]


def box_half_width_m(precision_bits: int, latitude_deg: float) -> float:
    """The one half-width a layer carries for a position box, metres: an inscribed square.

    Casey's decision 1 (b) on issue #44 for the x72y429 run: "an inscribed
    square that trims the long side". The value is the smaller of
    analyze.box_half_width_m's north-south and east-west halves, which at any
    latitude away from the equator is the east-west half, so the engine's
    square lies inside the reported box and leaves out the strips at its north
    and south ends. In EPSG:26915 the box is also turned by the grid
    convergence, about 1.6 degrees at x72y429, so the square's corners reach
    about 8 m past the box's east and west sides at 16 bits. Drawing the real
    rectangle with that rotation is a later unit (Casey's (c)), which removes
    that overshoot; this function is the only place the rule is written.

    Args:
        precision_bits: Bits of the 32-bit integer degrees the node kept.
        latitude_deg: Latitude of the box centre, degrees (EPSG:4326).

    Returns:
        The half-width in metres.
    """
    north_south_m, east_west_m = _spike().az.box_half_width_m(precision_bits, latitude_deg)
    return float(min(north_south_m, east_west_m))


def msa_contains() -> InArea:
    """The MSA's point test, from the polygon the spike reads (CBSA 41180, the dbt seed).

    Raises:
        SelectionError: if the spike scripts are not in the checkout.
    """
    contains: InArea = _msa(_spike()).contains
    return contains


def _msa(figures: ModuleType) -> Any:
    """The spike's Area for the MSA, whose `contains` takes 1e-7 degree integers."""
    return figures.az.Area("the MSA", figures.az.load_area(figures.MSA_CSV, {MSA_GEOID}))


def _spike() -> ModuleType:
    """The spike's figures module, which imports analyze.py as `az`.

    Raises:
        SelectionError: if the spike scripts are not in the checkout.
    """
    missing = [name for name in SPIKE_SCRIPTS if not (SPIKE_DIR / name).is_file()]
    if missing:
        raise SelectionError(
            f"{', '.join(missing)} not found in {SPIKE_DIR}; the pair rules run the ADR 0003 "
            "spike's scripts, so the command runs from a full checkout"
        )
    # The spike folder holds a capture.py and other plain module names, so it
    # is on the path only while figures and analyze are imported.
    added = str(SPIKE_DIR) not in sys.path
    if added:
        sys.path.insert(0, str(SPIKE_DIR))
    try:
        return importlib.import_module("figures")
    finally:
        if added:
            sys.path.remove(str(SPIKE_DIR))


LinkHour = tuple[int, int, int]


def _stationary_link_hours(
    figures: ModuleType, rows: Sequence[dict[str, Any]], ids: StandIns, broker: str, msa: Any
) -> dict[LinkHour, int]:
    """Link-hours of a run at 500 m under the stationarity rule, MSA, with their receipt counts."""
    az = figures.az

    def load(name: str, start_s: float, end_s: float, roots: object) -> Any:
        tally = az.Tally()
        flag = f"seen_{name}"
        for row in rows:
            if row[flag] and start_s <= row["received_at"].timestamp() < end_s:
                tally.add(_parsed(az, row, ids))
        return tally

    tally = figures.run_tallies(load, RUNS[broker])
    positions = az.area_positions(tally, msa)
    result = az.apply_rules(tally, positions, stationary=True)["combined"]
    return {key: len(result.windows[key]) for key in result.link_hours(az.MIN_DISTANCE_M)}


def _pair_activity(keys: dict[str, dict[LinkHour, int]], ids: StandIns) -> tuple[PairActivity, ...]:
    pairs: dict[tuple[int, int], dict[str, Any]] = {}
    for broker, link_hours in keys.items():
        for (relayer, gateway, hour), receipts in link_hours.items():
            found = pairs.setdefault(
                (relayer, gateway), {"hours": set(), "public": 0, "meshstl": 0, "receipts": 0}
            )
            found["hours"].add(hour)
            found[broker] += 1
            found["receipts"] += receipts
    activity = [
        PairActivity(
            relayer=ids.names[relayer],
            gateway=ids.names[gateway],
            link_hours_public=found["public"],
            link_hours_meshstl=found["meshstl"],
            link_hours_union=len(found["hours"]),
            receipts=found["receipts"],
        )
        for (relayer, gateway), found in pairs.items()
    ]
    return tuple(sorted(activity, key=lambda pair: (pair.relayer, pair.gateway)))


def _spike_status(row: dict[str, Any]) -> str:
    """The status analyze.py would have given: its "malformed" was a payload parse failure."""
    if row["decode_status"] == "malformed" and (row["portnum"] or 0) <= 0:
        return "unknown_port"
    status: str = row["decode_status"]
    return status


def _parsed(az: ModuleType, row: dict[str, Any], ids: StandIns) -> Any:
    """analyze.py's Parsed for one row: the gate's mapping, with the contract's positions."""
    topic, status = row["topic"], _spike_status(row)
    snr = row["rx_snr"]
    parsed = az.Parsed(
        row["received_at"].timestamp(), topic_class(topic), az.topic_region(topic), status,
        "stripped" if status == "stripped" else "",
        gateway=ids.of(row["gateway_pseudonym"], row["gateway_low_byte"]),
        sender=ids.of(row["origin_pseudonym"], row["origin_low_byte"]),
        packet_id=row["packet_id"] or 0, relay=row["relay_node"] or 0,
        hop_limit=row["hop_limit"], hop_start=row["hop_start"] or 0, via_mqtt=row["via_mqtt"],
        rx_snr=float("nan") if snr is None else float(snr), rx_rssi=row["rx_rssi"] or 0,
        portnum=row["portnum"],
    )  # fmt: skip
    parsed.root = topic_root(topic)
    box = position_of(row)
    if box is not None:
        place = tuple(box)
        if row["position_source"] == "position":
            parsed.position = place
        elif row["position_source"] == "mapreport":
            parsed.map_report = place
    if row["portnum"] == NEIGHBORINFO_APP and row["neighbour_pseudonyms"] is not None:
        parsed.neighbors = tuple(
            (ids.of(name, low), neighbour_snr)
            for name, low, neighbour_snr in zip(
                row["neighbour_pseudonyms"],
                row["neighbour_low_bytes"],
                row["neighbour_snrs"],
                strict=True,
            )
        )
    return parsed
