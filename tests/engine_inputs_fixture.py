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
- C to G3, G3 inside the tile 45 m from its east edge (its box plus 50 m
  reaches outside the DEM) and C east of the tile, public, 3 link-hours.
- D to E, both east of the tile, public, 1 link-hour.
- M to E, public, 1 link-hour; M sends two different boxes, so it is not
  stationary and its link rests on the 30-minute rule.
- F to G1, MeshSTL; F sends only 13-bit positions (coarse), so it never makes a
  pair.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pyproj import Transformer

from ingester.allowlist import DECODER_VERSION, SCHEMA_VERSION, allowlist
from ingester.parquet import write_rows
from pipeline.common import snapshot as common

SNAPSHOT_ID = "capture-fixture-2026-10-22"
TELEMETRY_APP = 67
POSITION_APP = 3
_TO_DEGREES = Transformer.from_crs(26915, 4326, always_xy=True)


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

    def box(self, east_shift_m: float = 0.0) -> tuple[int, int, int]:
        """(latitude_i, longitude_i, precision_bits) as firmware would send it."""
        lon, lat = _TO_DEGREES.transform(self.easting_m + east_shift_m, self.northing_m)
        bits = self.precision_bits
        return quantise(round(lat * 1e7), bits), quantise(round(lon * 1e7), bits), bits


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
        FakeNode("G3", 0x13, 729_950, 4_288_000, 18),
        FakeNode("C", 0x23, 738_000, 4_288_000, 16),
        FakeNode("D", 0x24, 740_000, 4_295_000, 16),
        FakeNode("E", 0x14, 745_000, 4_295_000, 16),
        FakeNode("M", 0x25, 742_000, 4_300_000, 16),
        FakeNode("F", 0x26, 723_000, 4_287_000, 13),
    )
}
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


def _positions(rows: RowMaker, moved_a: bool, one_coordinate: bool) -> None:
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
    for name, gateway in (("G3", "G3"), ("C", "G3"), ("D", "E"), ("E", "E")):
        rows.position(n[name], n[gateway], at(23, 10), PUBLIC, n[name].box())
    rows.position(n["M"], n["E"], at(23, 15, 20), PUBLIC, n["M"].box())
    rows.position(n["M"], n["E"], at(23, 20), PUBLIC, n["M"].box(east_shift_m=1_700))


def _links(rows: RowMaker) -> None:
    n = NODES
    for hour in (at(24, 2), at(24, 3)):
        rows.receipts(n["A"], n["G1"], hour, MESHSTL)
    rows.receipts(n["F"], n["G1"], at(24, 2), MESHSTL)
    rows.receipts(n["B"], n["G2"], at(24, 2), BOTH)
    for hour in (at(23, 11), at(23, 12), at(23, 13)):
        rows.receipts(n["B"], n["G2"], hour, PUBLIC)
        rows.receipts(n["C"], n["G3"], hour, PUBLIC)
    rows.receipts(n["D"], n["E"], at(23, 11), PUBLIC)
    rows.receipts(n["M"], n["E"], at(23, 15), PUBLIC)


def build_capture(root: Path, *, moved_a: bool = False, one_coordinate: bool = True) -> Path:
    """Write the synthetic capture snapshot under root and return its finished folder.

    Args:
        root: The snapshots folder.
        moved_a: Also give A a second box on the public broker, outside the
            MeshSTL window, so A is stationary in the MeshSTL run but not over
            the capture.
        one_coordinate: Include A's Position with no latitude.
    """
    rows = RowMaker()
    _positions(rows, moved_a, one_coordinate)
    _links(rows)
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
    metadata = {
        "snapshot_id": SNAPSHOT_ID,
        "capture_date": "2026-10-22",
        "source_url": "synthetic",
        "licence": "synthetic test data",
        "seeds": {},
    }
    common.write_manifest(partial, metadata, required=common.REQUIRED_METADATA)
    return common.finish_snapshot(partial)
