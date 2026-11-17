"""A synthetic capture snapshot for the engine input command: invented nodes and positions.

Nothing here comes from the real capture. Every pseudonym is the sha256 of an
invented name, every position is chosen in EPSG:26915 around the USGS 1 m tile
x72y429 and quantised the way firmware quantises a reduced-precision position,
and every row is written with the allowlist schema into a finished snapshot
folder with its manifest, as a capture or the reduction would leave it.

The links, relayer to gateway (positions in EPSG:26915 before quantisation):

- A to G1, both inside the tile, MeshSTL only, 2 link-hours. G1 sends at 20 bits
  on MeshSTL, so it is a precise source (`pos_04`). A also sends a Position at
  0, 0 and one with no latitude, neither of which is a position (`pos_01`,
  `pos_03`).
- B to G2, G2 inside the tile and B east of it, public, 4 link-hours, one of
  them carried by both brokers.
- C to G3, G3 inside the tile 300 m from its east edge and C east of the
  tile, public, 3 link-hours. With `g3_at_edge`, G3 sends a box 45 m from the
  edge instead, whose box plus 50 m reaches outside the DEM.
- D to E, both east of the tile, public, 1 link-hour. With `busy_outside_pair`,
  6 link-hours, which makes it the busiest pair by link-hours though it does
  not touch the tile.
- M to E, public, 1 link-hour; M sends two different boxes, so it is not
  stationary and its link rests on the 30-minute rule.
- F to G1, MeshSTL; F sends only 13-bit positions (coarse), so it never makes a
  pair.

With `straddler`, one node also sends a second box on the other side of the
tile edge (`STRADDLE_SHIFTS_M`), as a MeshSTL row outside the MeshSTL run's
window: neither spike run sees it, so the pairs and their link-hours stay as
above, while the node's boxes over the capture lie on both sides of the edge.

Three options exercise the amended `lnk_02` overlap rule (ADR 0003, Overlap
amendment, 2026-11-17), each adding boxes that the rule of 2026-10-31 would
refuse or count as straddling and the overlap rule places as before:

- `nested_g1`: G1 also sends its position at 15 bits on the public broker, a
  box holding its 20-bit MeshSTL box. G1 is in no public pair, so the spike's
  per-run rules give the same pairs.
- `nested_g3`: G3 also sends its position at 15 bits, a box holding its
  18-bit box whose centre lies east of the tile edge, as a MeshSTL row outside
  the MeshSTL run's window.
- `ignored_a`: A also sends a 16-bit box outside the MSA
  (`OUTSIDE_MSA_SHIFT_M`) and a coarse 13-bit box, as MeshSTL rows outside
  the MeshSTL run's window.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

from ingester.allowlist import DECODER_VERSION, SCHEMA_VERSION, allowlist
from ingester.parquet import write_rows
from pipeline.common import snapshot as common

SNAPSHOT_ID = "capture-fixture-2026-10-22"
DEM_SNAPSHOT_ID = "dem-fixture-x72y429"
# The real x72y429 DEM's bounds (issue #44, step 2), held by a 2 by 2 raster of
# 5006 m pixels: the command reads only the bounds.
DEM_BOUNDS_M = (719_994.0, 4_279_994.0, 730_006.0, 4_290_006.0)
TELEMETRY_APP = 67
POSITION_APP = 3
_TO_DEGREES = Transformer.from_crs(26915, 4326, always_xy=True)
_TO_METRES = Transformer.from_crs(4326, 26915, always_xy=True)


@dataclass(frozen=True)
class FakeNode:
    """An invented node with its position in EPSG:26915 and the precision it sends."""

    name: str
    low_byte: int
    easting_m: float
    northing_m: float
    precision_bits: int

    @property
    def pseudonym(self) -> str:
        return hashlib.sha256(f"fixture-node-{self.name}".encode()).hexdigest()

    def box(
        self, east_shift_m: float = 0.0, north_shift_m: float = 0.0, bits: int | None = None
    ) -> tuple[int, int, int]:
        """(latitude_i, longitude_i, precision_bits) as firmware would send it.

        Args:
            east_shift_m: Metres east of the node's position, EPSG:26915.
            north_shift_m: Metres north of the node's position, EPSG:26915.
            bits: The precision sent; the node's own when None.
        """
        lon, lat = _TO_DEGREES.transform(
            self.easting_m + east_shift_m, self.northing_m + north_shift_m
        )
        bits = self.precision_bits if bits is None else bits
        return quantise(round(lat * 1e7), bits), quantise(round(lon * 1e7), bits), bits


def centre_m(name: str) -> tuple[float, float]:
    """The quantised box centre of a fixture node in EPSG:26915 metres."""
    return box_centre_m(NODES[name].box())


def box_centre_m(box: tuple[int, int, int]) -> tuple[float, float]:
    """The centre of a (latitude_i, longitude_i, precision_bits) box in EPSG:26915 metres."""
    latitude_i, longitude_i, _ = box
    easting_m, northing_m = _TO_METRES.transform(longitude_i / 1e7, latitude_i / 1e7)
    return float(easting_m), float(northing_m)


def every_box() -> list[tuple[int, int, int]]:
    """Every box the fixture can send: each node's own and every box an option adds."""
    boxes = [node.box() for node in NODES.values()]
    boxes += [NODES[name].box(*shift) for name, shift in STRADDLE_SHIFTS_M.items()]
    boxes += [NODES["G1"].box(bits=WIDE_BITS), NODES["G3"].box(bits=WIDE_BITS)]
    boxes += [NODES["A"].box(east_shift_m=OUTSIDE_MSA_SHIFT_M), NODES["A"].box(bits=COARSE_BITS)]
    return [*boxes, NODES["G3"].box(east_shift_m=G3_EDGE_SHIFT_M)]


def quantise(value: int, bits: int) -> int:
    """Keep the top bits of a 1e-7 degree integer and centre it in its box, as firmware does."""
    shift = 32 - bits
    return ((value >> shift) << shift) + (1 << (shift - 1))


NODES = {
    node.name: node
    for node in (
        FakeNode("G1", 0x11, 724_000, 4_285_000, 20),
        FakeNode("A", 0x21, 726_500, 4_286_500, 16),
        FakeNode("G2", 0x12, 722_000, 4_283_000, 18),
        FakeNode("B", 0x22, 735_000, 4_283_000, 16),
        FakeNode("G3", 0x13, 729_700, 4_288_000, 18),
        FakeNode("C", 0x23, 738_000, 4_288_000, 16),
        FakeNode("D", 0x24, 740_000, 4_295_000, 16),
        FakeNode("E", 0x14, 745_000, 4_295_000, 16),
        FakeNode("M", 0x25, 742_000, 4_300_000, 16),
        FakeNode("F", 0x26, 723_000, 4_287_000, 13),
    )
}
# East and north shifts, metres, that put a node's second box across the
# tile edge: G2 (a gateway-only pair's gateway) and A (the both-ends pair's
# relayer) east of the tile, D (an unselected pair's relayer) and B (a
# gateway-only pair's relayer) inside it.
STRADDLE_SHIFTS_M = {
    "G2": (10_000.0, 0.0),
    "A": (5_000.0, 0.0),
    "D": (-15_000.0, -10_000.0),
    "B": (-10_000.0, 0.0),
}
# G3's box 45 m from the tile's east edge, as the fixture first placed it.
G3_EDGE_SHIFT_M = 250.0
# The precision of the boxes nested_g1 and nested_g3 add: usable, and wide
# enough that G3's 15-bit box centre lies east of the tile edge.
WIDE_BITS = 15
COARSE_BITS = 13
# 300 km west of A, in western Missouri: outside the MSA, inside UTM zone 15.
OUTSIDE_MSA_SHIFT_M = -300_000.0
PUBLIC = {"seen_public": True, "seen_meshstl": False}
MESHSTL = {"seen_public": False, "seen_meshstl": True}
BOTH = {"seen_public": True, "seen_meshstl": True}


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


class RowMaker:
    """Allowlisted delivery rows with unique delivery keys and packet ids."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.serial = 0

    def _row(self, sender: FakeNode, gateway: FakeNode, when: datetime, **fields: Any) -> None:
        self.serial += 1
        row: dict[str, Any] = dict.fromkeys(allowlist("mqtt_message"))
        row.update(
            snapshot_id=SNAPSHOT_ID,
            delivery_key=hashlib.sha256(f"delivery-{self.serial}".encode()).hexdigest(),
            received_at=when,
            topic=f"msh/US/MeshSTL/2/e/LongFast/{gateway.pseudonym}",
            decode_status="ok",
            gateway_pseudonym=gateway.pseudonym,
            gateway_low_byte=gateway.low_byte,
            origin_pseudonym=sender.pseudonym,
            origin_low_byte=sender.low_byte,
            packet_id=1000 + self.serial,
            hop_limit=0,
            via_mqtt=False,
            payload_length=20,
            decoder_version=DECODER_VERSION,
            schema_version=SCHEMA_VERSION,
        )
        row.update(fields)
        self.rows.append(row)

    def receipts(
        self, sender: FakeNode, gateway: FakeNode, hour: datetime, flags: dict[str, bool]
    ) -> None:
        """Three direct-hop radio receipts in one clock hour: one link-hour."""
        for minute in (5, 20, 40):
            self._row(
                sender,
                gateway,
                hour + timedelta(minutes=minute),
                relay_node=sender.low_byte,
                rx_rssi=-100,
                rx_snr=5.0,
                portnum=TELEMETRY_APP,
                **flags,
            )

    def position(
        self,
        sender: FakeNode,
        gateway: FakeNode,
        when: datetime,
        flags: dict[str, bool],
        place: tuple[int | None, int | None, int],
    ) -> None:
        """A Position packet heard by radio, carrying no relay_node so it is no receipt."""
        latitude_i, longitude_i, bits = place
        self._row(
            sender,
            gateway,
            when,
            portnum=POSITION_APP,
            position_source="position",
            latitude_i=latitude_i,
            longitude_i=longitude_i,
            precision_bits=bits,
            **flags,
        )


def _positions(rows: RowMaker, moved_a: bool, one_coordinate: bool, g3_at_edge: bool) -> None:
    n = NODES
    for when in (at(24, 1), at(24, 4)):
        rows.position(n["G1"], n["G1"], when, MESHSTL, n["G1"].box())
        rows.position(n["A"], n["G1"], when, MESHSTL, n["A"].box())
        rows.position(n["F"], n["G1"], when, MESHSTL, n["F"].box())
    rows.position(n["A"], n["G1"], at(24, 5), MESHSTL, (0, 0, 16))
    if one_coordinate:
        rows.position(n["A"], n["G1"], at(24, 6), MESHSTL, (None, n["A"].box()[1], 16))
    if moved_a:
        rows.position(n["A"], n["G2"], at(23, 9), PUBLIC, n["A"].box(east_shift_m=2_000))
    for name, gateway in (("G2", "G2"), ("B", "G2")):
        rows.position(n[name], n[gateway], at(24, 1, 50), BOTH, n[name].box())
    g3_shift_m = G3_EDGE_SHIFT_M if g3_at_edge else 0.0
    rows.position(n["G3"], n["G3"], at(23, 10), PUBLIC, n["G3"].box(east_shift_m=g3_shift_m))
    for name, gateway in (("C", "G3"), ("D", "E"), ("E", "E")):
        rows.position(n[name], n[gateway], at(23, 10), PUBLIC, n[name].box())
    rows.position(n["M"], n["E"], at(23, 15, 20), PUBLIC, n["M"].box())
    rows.position(n["M"], n["E"], at(23, 20), PUBLIC, n["M"].box(east_shift_m=1_700))


def _links(rows: RowMaker, busy_outside_pair: bool) -> None:
    n = NODES
    for hour in (at(24, 2), at(24, 3)):
        rows.receipts(n["A"], n["G1"], hour, MESHSTL)
    rows.receipts(n["F"], n["G1"], at(24, 2), MESHSTL)
    rows.receipts(n["B"], n["G2"], at(24, 2), BOTH)
    for hour in (at(23, 11), at(23, 12), at(23, 13)):
        rows.receipts(n["B"], n["G2"], hour, PUBLIC)
        rows.receipts(n["C"], n["G3"], hour, PUBLIC)
    rows.receipts(n["D"], n["E"], at(23, 11), PUBLIC)
    if busy_outside_pair:
        for hour in range(16, 21):
            rows.receipts(n["D"], n["E"], at(23, hour), PUBLIC)
    rows.receipts(n["M"], n["E"], at(23, 15), PUBLIC)


def _overlap_options(rows: RowMaker, nested_g1: bool, nested_g3: bool, ignored_a: bool) -> None:
    """Boxes the overlap rule places as before (module docstring)."""
    n = NODES
    if nested_g1:
        rows.position(n["G1"], n["G1"], at(23, 9), PUBLIC, n["G1"].box(bits=WIDE_BITS))
    if nested_g3:
        rows.position(n["G3"], n["G3"], at(25, 1), MESHSTL, n["G3"].box(bits=WIDE_BITS))
    if ignored_a:
        outside = n["A"].box(east_shift_m=OUTSIDE_MSA_SHIFT_M)
        rows.position(n["A"], n["G1"], at(25, 1), MESHSTL, outside)
        rows.position(n["A"], n["G1"], at(25, 2), MESHSTL, n["A"].box(bits=COARSE_BITS))


def position_row(name: str, place: tuple[int, int, int], meshstl: bool = False) -> dict[str, Any]:
    """The fields of a Position row that node positions read, for tests without a snapshot."""
    latitude_i, longitude_i, bits = place
    return {
        "origin_pseudonym": NODES[name].pseudonym,
        "position_source": "position",
        "latitude_i": latitude_i,
        "longitude_i": longitude_i,
        "precision_bits": bits,
        "seen_public": not meshstl,
        "seen_meshstl": meshstl,
    }


def _straddle(rows: RowMaker, name: str) -> None:
    """A second box across the tile edge, after the MeshSTL run's window closes."""
    node = NODES[name]
    east_m, north_m = STRADDLE_SHIFTS_M[name]
    rows.position(node, node, at(25, 1), MESHSTL, node.box(east_m, north_m))


def build_capture(
    root: Path,
    *,
    moved_a: bool = False,
    one_coordinate: bool = True,
    g3_at_edge: bool = False,
    straddler: str | None = None,
    busy_outside_pair: bool = False,
    nested_g1: bool = False,
    nested_g3: bool = False,
    ignored_a: bool = False,
) -> Path:
    """Write the synthetic capture snapshot under root and return its finished folder.

    Args:
        root: The snapshots folder.
        moved_a: Also give A a second box on the public broker, outside the
            MeshSTL window, 2 km east and still inside the tile, so A is
            stationary in the MeshSTL run but not over the capture.
        one_coordinate: Include A's Position with no latitude.
        g3_at_edge: Place G3 45 m from the tile's east edge, where its box plus
            50 m reaches outside the DEM.
        straddler: A key of STRADDLE_SHIFTS_M: that node also sends a box on
            the other side of the tile edge.
        busy_outside_pair: Give D to E, which does not touch the tile, 5 more
            link-hours, so it is the busiest pair by link-hours.
        nested_g1: G1 also sends a 15-bit box holding its 20-bit box.
        nested_g3: G3 also sends a 15-bit box, centred east of the tile edge.
        ignored_a: A also sends a box outside the MSA and a coarse box.
    """
    rows = RowMaker()
    _positions(rows, moved_a, one_coordinate, g3_at_edge)
    _overlap_options(rows, nested_g1, nested_g3, ignored_a)
    if straddler is not None:
        _straddle(rows, straddler)
    _links(rows, busy_outside_pair)
    partial = common.start_snapshot(root, SNAPSHOT_ID)
    by_hour: dict[datetime, list[dict[str, Any]]] = {}
    for row in rows.rows:
        hour = row["received_at"].replace(minute=0, second=0, microsecond=0)
        by_hour.setdefault(hour, []).append(row)
    for hour, hour_rows in sorted(by_hour.items()):
        write_rows(
            partial / "mqtt_message" / f"{hour:%Y-%m-%dT%H}.parquet", "mqtt_message", hour_rows
        )
    # The reproduction gate reads these two files; the command does not.
    for name in ("coverage.json", "coverage_roots.json"):
        (partial / name).write_text(json.dumps({}), encoding="utf-8")
    return _finish(partial, SNAPSHOT_ID)


def _finish(partial: Path, snapshot_id: str) -> Path:
    metadata = {
        "snapshot_id": snapshot_id,
        "capture_date": "2026-10-22",
        "source_url": "synthetic",
        "licence": "synthetic test data",
        "seeds": {},
    }
    common.write_manifest(partial, metadata, required=common.REQUIRED_METADATA)
    return common.finish_snapshot(partial)


def build_dem(root: Path) -> Path:
    """Write a DEM snapshot with the x72y429 DEM's bounds under root and return its folder."""
    partial = common.start_snapshot(root, DEM_SNAPSHOT_ID)
    west, _, east, north = DEM_BOUNDS_M
    pixel_m = (east - west) / 2
    profile = {
        "driver": "GTiff",
        "width": 2,
        "height": 2,
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:26915",
        "transform": from_origin(west, north, pixel_m, pixel_m),
        "nodata": -9999.0,
    }
    with rasterio.open(partial / "dem.tif", "w", **profile) as dataset:
        dataset.write(np.full((1, 2, 2), 150.0, dtype="float32"))
    return _finish(partial, DEM_SNAPSHOT_ID)
