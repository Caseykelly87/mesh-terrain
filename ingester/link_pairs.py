"""The pairs positioned at both ends in a capture snapshot, by the ADR 0003 spike's own rules.

A pair is a (relayer, gateway) link with at least one link-hour at 500 m or
more in the MSA under the stationarity rule, in the public broker's whole
capture or the MeshSTL broker's 24 hours, exactly as the reproduction gate
counts them (`docs/adr/0003-spike/reproduce_from_snapshot.py`,
`stationary_pairs`): this module runs the spike's `analyze.py` on the
snapshot rows rather than porting it, so the set is the spike's by
construction. As in the spike, relayer candidates and the stationarity test
see the whole run, not the trailing 24 hours and 7 days the warehouse applies
(ADR 0003, Terms).

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
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, NamedTuple

import pyarrow.parquet as pq
import structlog

from ingester.topics import topic_class, topic_root
from pipeline.common.snapshot import verified_manifest

log = structlog.get_logger(component="ingester.link_pairs")

REPO_ROOT = Path(__file__).resolve().parents[1]
SPIKE_DIR = REPO_ROOT / "docs" / "adr" / "0003-spike"
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


class Box(NamedTuple):
    """A reported position box: 1e-7 degree integers (EPSG:4326) and the bits kept."""

    latitude_i: int
    longitude_i: int
    precision_bits: int


@dataclass(frozen=True)
class NodePositions:
    """Every position a node sent in the capture, either broker, any area, either source."""

    boxes: frozenset[Box]
    precise_source: bool

    @property
    def stationary_box(self) -> Box | None:
        """The node's one box when every position it sent is that box and it is usable (lnk_02)."""
        if len(self.boxes) != 1:
            return None
        (box,) = self.boxes
        return box if box.precision_bits >= USABLE_BITS else None


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
    """
    figures = _spike()
    ids = StandIns()
    keys = {broker: _stationary_link_hours(figures, rows, ids, broker) for broker in RUNS}
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
    return CaptureLinks(pairs, counts, node_positions(rows), len(rows), missing)


def node_positions(rows: Iterable[dict[str, Any]]) -> dict[str, NodePositions]:
    """Every node's boxes over the whole capture, and whether one is a precise source (pos_04)."""
    boxes: dict[str, set[Box]] = {}
    precise: set[str] = set()
    for row in rows:
        box = position_of(row)
        if box is None:
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


def spike_box_half_width_m(precision_bits: int, latitude_deg: float) -> float:
    """Half the side of the square that holds a position box, metres: analyze.py's larger half.

    analyze.box_half_width_m gives the box's north-south and east-west
    half-widths; a square of the larger one holds the whole box, so a layer
    that carries one half-width never makes a position sharper than sent.

    Args:
        precision_bits: Bits of the 32-bit integer degrees the node kept.
        latitude_deg: Latitude of the box centre, degrees (EPSG:4326).
    """
    north_south_m, east_west_m = _spike().az.box_half_width_m(precision_bits, latitude_deg)
    return float(max(north_south_m, east_west_m))


def _spike() -> ModuleType:
    """The spike's figures module, which imports analyze.py as `az`."""
    if str(SPIKE_DIR) not in sys.path:
        sys.path.insert(0, str(SPIKE_DIR))
    return importlib.import_module("figures")


LinkHour = tuple[int, int, int]


def _stationary_link_hours(
    figures: ModuleType, rows: Sequence[dict[str, Any]], ids: StandIns, broker: str
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
    msa = az.Area("the MSA", az.load_area(figures.MSA_CSV, {MSA_GEOID}))
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
