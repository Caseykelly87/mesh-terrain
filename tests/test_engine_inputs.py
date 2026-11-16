"""The engine input command: real site and receiver layers from a capture snapshot.

Every test runs on the synthetic capture in tests/engine_inputs_fixture.py;
none reads the real snapshot.
"""

import re
import sys
from pathlib import Path

import geopandas as gpd
import pytest
from engine_fixture import no_logger_caching
from engine_inputs_fixture import NODES, SNAPSHOT_ID, build_capture
from pyproj import Transformer
from structlog.testing import capture_logs

from engine.grid import run_grid
from engine.points import Endpoint, read_points
from ingester import engine_inputs
from ingester.engine_inputs import (
    SelectionError,
    busiest_two,
    inside_dem,
    parse_tile,
    select_nodes,
)
from ingester.link_pairs import (
    Box,
    CaptureLinks,
    NodePositions,
    PairActivity,
    capture_links,
    read_capture_rows,
)
from terrain.draws import EndpointBox

# The import above activates the autouse fixture for this module.
_ = no_logger_caching

DEM_EXTENT = ("719994", "4279994", "730006", "4290006")
SPIKE_DIR = Path(__file__).resolve().parents[1] / "docs" / "adr" / "0003-spike"
_TO_METRES = Transformer.from_crs(4326, 26915, always_xy=True)


def pseudonym(name: str) -> str:
    return NODES[name].pseudonym


def centre_m(name: str) -> tuple[float, float]:
    latitude_i, longitude_i, _ = NODES[name].box()
    x, y = _TO_METRES.transform(longitude_i / 1e7, latitude_i / 1e7)
    return float(x), float(y)


def run(tmp_path: Path, *extra: str, expect_pairs: str = "5", out: Path | None = None) -> int:
    out = out or tmp_path / "configs" / "x72y429"
    argv = ["--snapshot", SNAPSHOT_ID, "--snapshots-dir", str(tmp_path / "snapshots")]
    argv += ["--tile", "x72y429", "--out", str(out), "--expect-pairs", expect_pairs]
    argv += ["--dem-extent", *DEM_EXTENT, *extra]
    return engine_inputs.main(argv)


@pytest.fixture
def capture(tmp_path: Path) -> Path:
    return build_capture(tmp_path / "snapshots")


def test_tile_bounds_follow_the_usgs_naming() -> None:
    tile = parse_tile("x72y429")
    assert (tile.west_m, tile.south_m, tile.east_m, tile.north_m) == (
        720_000.0,
        4_280_000.0,
        730_000.0,
        4_290_000.0,
    )
    assert tile.contains(720_000.0, 4_289_999.0)
    assert not tile.contains(730_000.0, 4_285_000.0)


@pytest.mark.parametrize("name", ["x72", "X72Y429", "x72y429 ", "y429x72"])
def test_tile_refuses_other_names(name: str) -> None:
    with pytest.raises(SelectionError):
        parse_tile(name)


def test_pairs_follow_the_stationarity_rule(capture: Path) -> None:
    links = capture_links(read_capture_rows(capture))
    pairs = {(pair.relayer, pair.gateway): pair for pair in links.pairs}
    expected = {("A", "G1"), ("B", "G2"), ("C", "G3"), ("D", "E"), ("M", "E")}
    assert set(pairs) == {(pseudonym(r), pseudonym(g)) for r, g in expected}
    assert links.pair_counts == {"public": 4, "meshstl": 2, "shared": 1, "union": 5}
    busy = pairs[(pseudonym("B"), pseudonym("G2"))]
    assert (busy.link_hours_public, busy.link_hours_meshstl) == (4, 1)
    assert (busy.link_hours_union, busy.receipts) == (4, 15)


def test_a_coarse_node_makes_no_pair(capture: Path) -> None:
    links = capture_links(read_capture_rows(capture))
    assert all(pseudonym("F") not in (pair.relayer, pair.gateway) for pair in links.pairs)


def test_a_moving_node_is_not_stationary(capture: Path) -> None:
    nodes = capture_links(read_capture_rows(capture)).nodes
    assert len(nodes[pseudonym("M")].boxes) == 2
    assert nodes[pseudonym("M")].stationary_box is None
    assert nodes[pseudonym("G2")].stationary_box == Box(*NODES["G2"].box())


def test_positions_at_zero_or_with_one_coordinate_are_not_positions(capture: Path) -> None:
    links = capture_links(read_capture_rows(capture))
    assert links.nodes[pseudonym("A")].boxes == frozenset({Box(*NODES["A"].box())})
    assert links.one_coordinate_missing == 1


def test_precise_source_needs_twenty_bits_from_meshstl(capture: Path) -> None:
    nodes = capture_links(read_capture_rows(capture)).nodes
    assert nodes[pseudonym("G1")].precise_source
    assert not nodes[pseudonym("A")].precise_source
    assert not nodes[pseudonym("G2")].precise_source


def test_pair_counts_equal_the_reproduction_gate(tmp_path: Path) -> None:
    folder = build_capture(tmp_path / "snapshots", one_coordinate=False)
    sys.path.insert(0, str(SPIKE_DIR))
    try:
        import reproduce_from_snapshot as gate
    finally:
        sys.path.remove(str(SPIKE_DIR))
    snapshot = gate.Snapshot(folder)
    public = gate.stationary_pairs(snapshot, "public_full")
    meshstl = gate.stationary_pairs(snapshot, "meshstl_24h")
    links = capture_links(read_capture_rows(folder))
    assert links.pair_counts == {
        "public": len(public),
        "meshstl": len(meshstl),
        "shared": len(public & meshstl),
        "union": len(public | meshstl),
    }


def activity(name: str, union: int, total: int, receipts: int) -> PairActivity:
    return PairActivity(
        relayer=f"r-{name}",
        gateway=f"g-{name}",
        link_hours_public=total,
        link_hours_meshstl=0,
        link_hours_union=union,
        receipts=receipts,
    )


def test_busiest_two_when_every_measure_agrees() -> None:
    pairs = [activity("a", 2, 2, 6), activity("b", 5, 6, 18), activity("c", 4, 4, 12)]
    assert [pair.relayer for pair in busiest_two(pairs)] == ["r-b", "r-c"]


def test_busiest_two_refuses_when_measures_disagree() -> None:
    pairs = [activity("a", 3, 3, 30), activity("b", 5, 6, 18), activity("c", 4, 4, 12)]
    with pytest.raises(SelectionError, match="measure"):
        busiest_two(pairs)


def test_busiest_two_refuses_a_tie_for_second() -> None:
    pairs = [activity("a", 4, 4, 12), activity("b", 5, 6, 18), activity("c", 4, 4, 12)]
    with pytest.raises(SelectionError, match="tie"):
        busiest_two(pairs)


def test_a_gateway_in_two_selected_pairs_is_one_site_with_the_both_ends_role() -> None:
    def pair(relayer: str, gateway: str, link_hours: int) -> PairActivity:
        return PairActivity(
            pseudonym(relayer), pseudonym(gateway), link_hours, 0, link_hours, 3 * link_hours
        )

    pairs = (pair("A", "G1", 2), pair("B", "G1", 5), pair("C", "G3", 4), pair("D", "E", 1))
    nodes = {
        node.pseudonym: NodePositions(frozenset({Box(*node.box())}), False)
        for node in NODES.values()
    }
    links = CaptureLinks(pairs, {}, nodes, 0, 0)
    sites, receiver = select_nodes(links, parse_tile("x72y429"), expect_pairs=4)
    assert [(site.pseudonym, site.role) for site in sites] == [
        (pseudonym("G1"), "both_ends_gateway"),
        (pseudonym("G3"), "busy_pair_gateway"),
    ]
    assert receiver.pseudonym == pseudonym("A")


def test_extent_check_is_the_engines() -> None:
    extent = (719_994.0, 4_279_994.0, 730_006.0, 4_290_006.0)
    half_width_m = 364.8
    edge_m = 720_000.0 + half_width_m + 50.0
    assert inside_dem((edge_m, 4_285_000.0), half_width_m, extent)
    assert not inside_dem((edge_m - 1.0, 4_285_000.0), half_width_m, extent)
    grid = run_grid(
        [
            Endpoint(
                endpoint_id="x",
                box=EndpointBox(centre_xy_m=(edge_m - 1.0, 4_285_000.0), half_width_m=half_width_m),
            )
        ],
        resolution_m=1.0,
    )
    assert grid.west_m < extent[0]


def test_command_writes_the_layers(
    tmp_path: Path, capture: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path) == 0
    out = tmp_path / "configs" / "x72y429"
    sites = gpd.read_parquet(out / "tx_sites_real.parquet")
    receivers = gpd.read_parquet(out / "rx_real.parquet")
    assert list(sites.columns) == ["site_id", "geometry", "box_half_width_m", "role"]
    assert list(receivers.columns) == ["rx_id", "geometry", "box_half_width_m"]
    assert sites.crs.to_epsg() == receivers.crs.to_epsg() == 26915
    assert list(sites["site_id"]) == [pseudonym("G1"), pseudonym("G2"), pseudonym("G3")]
    assert list(sites["role"]) == ["both_ends_gateway", "busy_pair_gateway", "busy_pair_gateway"]
    assert list(receivers["rx_id"]) == [pseudonym("A")]
    for frame, column in ((sites, "site_id"), (receivers, "rx_id")):
        for row_id, point in zip(frame[column], frame.geometry, strict=True):
            name = next(key for key, node in NODES.items() if node.pseudonym == row_id)
            assert (point.x, point.y) == pytest.approx(centre_m(name), abs=1e-6)
    assert str(sites["box_half_width_m"].dtype) == "float64"
    assert list(sites["box_half_width_m"].round(1)) == [22.8, 91.2, 91.2]
    assert list(receivers["box_half_width_m"].round(1)) == [364.8]
    assert len(read_points(out / "tx_sites_real.parquet", "site_id")) == 3
    assert len(read_points(out / "rx_real.parquet", "rx_id")) == 1
    printed = capsys.readouterr().out
    assert f"{pseudonym('G3')} busy_pair_gateway" in printed
    assert re.search(rf"{pseudonym('G3')} .*extent fail", printed)
    assert re.search(rf"{pseudonym('G1')} .*extent pass", printed)
    assert re.search(rf"{pseudonym('A')} .*extent pass", printed)


def test_command_prints_and_logs_no_coordinate(
    tmp_path: Path, capture: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with capture_logs() as events:
        assert run(tmp_path) == 0
    text = capsys.readouterr().out + repr(events)
    numbers = [f"{value:.0f}" for name in NODES for value in centre_m(name)]
    numbers += [str(part) for node in NODES.values() for part in node.box()[:2]]
    assert not [number for number in numbers if number in text]
    assert not re.search(r"-?\b(38|90)\.\d", text)


def test_command_refuses_a_node_that_moved_during_the_capture(tmp_path: Path) -> None:
    build_capture(tmp_path / "snapshots", moved_a=True)
    assert run(tmp_path) == 2
    assert not (tmp_path / "configs" / "x72y429").exists() or not any(
        (tmp_path / "configs" / "x72y429").iterdir()
    )


def test_command_refuses_an_unexpected_pair_count(tmp_path: Path, capture: Path) -> None:
    assert run(tmp_path, expect_pairs="8") == 2
    assert not (tmp_path / "configs" / "x72y429" / "tx_sites_real.parquet").exists()


def test_command_refuses_to_overwrite(tmp_path: Path, capture: Path) -> None:
    out = tmp_path / "configs" / "x72y429"
    out.mkdir(parents=True)
    existing = out / "rx_real.parquet"
    existing.write_bytes(b"earlier")
    assert run(tmp_path) == 2
    assert existing.read_bytes() == b"earlier"
    assert not (out / "tx_sites_real.parquet").exists()


def test_command_refuses_an_out_folder_inside_a_checkout(tmp_path: Path, capture: Path) -> None:
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    out = checkout / "configs"
    assert run(tmp_path, out=out) == 2
    assert not out.exists()


def test_command_refuses_a_changed_snapshot(tmp_path: Path, capture: Path) -> None:
    hour_file = sorted((capture / "mqtt_message").glob("*.parquet"))[0]
    hour_file.write_bytes(hour_file.read_bytes() + b"\0")
    assert run(tmp_path) == 2
