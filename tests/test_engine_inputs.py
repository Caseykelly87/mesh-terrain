"""The engine input command: real site and receiver layers from a capture snapshot.

Every test runs on the synthetic capture and DEM in tests/engine_inputs_fixture.py;
none reads the real snapshot.
"""

import math
import re
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pyarrow.parquet as pq
import pytest
from engine_fixture import no_logger_caching
from engine_inputs_fixture import (
    DEM_BOUNDS_M,
    DEM_SNAPSHOT_ID,
    G3_MOVED_SHIFT_M,
    NARROW_DEM_BOUNDS_M,
    NODES,
    PARTNERS,
    RECTANGLE_DEM_BOUNDS_M,
    SNAPSHOT_ID,
    box_centre_m,
    build_capture,
    build_dem,
    centre_m,
    every_box,
)
from prepare_snapshots import write_raster_snapshot
from structlog.testing import capture_logs

from engine import positions_marker
from engine.points import box_form, read_points
from engine.prepare import command as prepare_command
from engine.prepare.run_config import TILE_SNAPSHOTS
from engine.prepared import STAMP_FILE, require_admissible_layer
from ingester import engine_inputs, link_pairs
from ingester.link_cells import LinkBox, cell_of
from ingester.link_pairs import CaptureLinks, MovedPair, PairActivity, SelectionError
from ingester.tile_selection import (
    FAILS_DEM,
    MORE_THAN_ONE_BOX,
    OTHER_PAIR,
    STRADDLES,
    DroppedPair,
    PlacedNode,
    Selection,
    SiteSelection,
)
from terrain.boxes import EdgeBox

# The import above activates the autouse fixture for this module.
_ = no_logger_caching


def pseudonym(name: str) -> str:
    return NODES[name].pseudonym


# The both-ends gateway, then the gateway-only pairs' gateways in the capture's
# pair order, which sorts by relayer pseudonym: C to G3 before B to G2.
SITE_ORDER = ("G1", "G3", "G2")
SITE_ROLES = ["both_ends_gateway", "gateway_only_gateway", "gateway_only_gateway"]


# The lnk_02 box's four edges, as the real layers carry them (ADR 0008).
EDGE_COLUMNS = ["box_south_i", "box_north_i", "box_west_i", "box_east_i"]


def link_box(name: str) -> LinkBox:
    """A node's own lnk_02 box."""
    return cell_of(*NODES[name].box())


def edges_of(name: str) -> list[int]:
    box = link_box(name)
    return [box.south_i, box.north_i, box.west_i, box.east_i]


def edge_box(name: str) -> EdgeBox:
    south_i, north_i, west_i, east_i = edges_of(name)
    return EdgeBox(south_i=south_i, north_i=north_i, west_i=west_i, east_i=east_i)


def assert_edges(frame: gpd.GeoDataFrame, names_in_order: tuple[str, ...]) -> None:
    """Each row carries its node's LinkBox edges as int64 and sits within 1 mm of their centre."""
    assert "box_half_width_m" not in frame.columns
    for column in EDGE_COLUMNS:
        assert str(frame[column].dtype) == "int64", column
    rows = [[int(value) for value in row] for row in frame[EDGE_COLUMNS].itertuples(index=False)]
    assert rows == [edges_of(name) for name in names_in_order]
    for point, name in zip(frame.geometry, names_in_order, strict=True):
        assert math.dist((point.x, point.y), edge_box(name).centre_xy_m) <= 1e-3


def run(
    tmp_path: Path, expect_pairs: str = "5", out: Path | None = None, flags: tuple[str, ...] = ()
) -> int:
    out = out or tmp_path / "configs" / "x72y429"
    argv = ["--snapshot", SNAPSHOT_ID, "--snapshots-dir", str(tmp_path / "snapshots")]
    argv += ["--tile", "x72y429", "--dem-snapshot", DEM_SNAPSHOT_ID, "--out", str(out)]
    return engine_inputs.main([*argv, "--expect-pairs", expect_pairs, *flags])


def coordinates_in(text: str) -> list[str]:
    """Any box centre the fixture can send, in metres or 1e-7 degrees, or a St. Louis degree."""
    numbers = [f"{value:.0f}" for box in every_box() for value in box_centre_m(box)]
    numbers += [str(part) for box in every_box() for part in box[:2]]
    found = [number for number in numbers if number in text]
    return found + re.findall(r"-?\b(?:38|90)\.\d+", text)


@pytest.fixture
def capture(tmp_path: Path) -> Path:
    build_dem(tmp_path / "snapshots")
    return build_capture(tmp_path / "snapshots")


def test_command_writes_the_layers(
    tmp_path: Path, capture: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path) == 0
    out = tmp_path / "configs" / "x72y429"
    assert sorted(path.name for path in out.iterdir()) == [
        "engine_inputs_report.md",
        "rx_real.parquet",
        "tx_sites_real.parquet",
    ]
    sites = gpd.read_parquet(out / "tx_sites_real.parquet")
    receivers = gpd.read_parquet(out / "rx_real.parquet")
    assert list(sites.columns) == ["site_id", "geometry", *EDGE_COLUMNS, "role"]
    assert list(receivers.columns) == ["rx_id", "geometry", *EDGE_COLUMNS]
    assert sites.crs.to_epsg() == receivers.crs.to_epsg() == 26915
    assert list(sites["site_id"]) == [pseudonym(name) for name in SITE_ORDER]
    assert list(sites["role"]) == SITE_ROLES
    assert list(receivers["rx_id"]) == [pseudonym("A")]
    for frame, column in ((sites, "site_id"), (receivers, "rx_id")):
        for row_id, point in zip(frame[column], frame.geometry, strict=True):
            name = next(key for key, node in NODES.items() if node.pseudonym == row_id)
            assert (point.x, point.y) == pytest.approx(centre_m(name), abs=1e-6)
    assert_edges(sites, SITE_ORDER)
    assert_edges(receivers, ("A",))
    for layer, id_column, count in (("tx_sites_real", "site_id", 3), ("rx_real", "rx_id", 1)):
        endpoints = read_points(out / f"{layer}.parquet", id_column)
        assert len(endpoints) == count
        assert box_form(endpoints) == "rectangle"
    printed = capsys.readouterr().out
    assert re.search(rf"{pseudonym('G3')} gateway_only_gateway: .*extent pass", printed)
    assert re.search(rf"{pseudonym('G1')} both_ends_gateway: .*extent pass", printed)
    assert re.search(rf"{pseudonym('A')} both_ends_relayer: .*extent pass", printed)
    assert not any(line.startswith("dropped") for line in printed.splitlines())


def test_both_layers_are_marked_real(tmp_path: Path, capture: Path) -> None:
    assert run(tmp_path) == 0
    out = tmp_path / "configs" / "x72y429"
    for name in ("tx_sites_real.parquet", "rx_real.parquet"):
        metadata = pq.read_metadata(out / name).metadata
        assert metadata[b"mesh_terrain:positions"] == b"real"
        assert b"geo" in metadata


def test_the_marker_matches_the_engines_definition() -> None:
    """Both packages spell the marker on their own; this keeps the spellings equal."""
    assert engine_inputs.POSITIONS_KEY == positions_marker.POSITIONS_KEY.encode()
    assert engine_inputs.POSITIONS_REAL == positions_marker.REAL.encode()


def test_the_command_states_the_site_rule_and_no_busiest_pairs(
    tmp_path: Path, capture: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path) == 0
    lines = capsys.readouterr().out.splitlines()
    assert (
        "sites by tile membership: the in-tile gateway of every pair touching the tile, "
        "the both-ends pair's first (Casey, issue #44, busiest pair, 2026-11-17)"
    ) in lines
    assert not any(line.startswith("busiest") for line in lines)


def test_the_busiest_pair_outside_the_tile_leaves_the_sites_as_they_are(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """D to E, the busiest pair by link-hours, does not touch the tile; the run still writes."""
    build_dem(tmp_path / "snapshots")
    build_capture(tmp_path / "snapshots", busy_outside_pair=True)
    assert run(tmp_path) == 0
    printed = capsys.readouterr().out
    assert f"pair {pseudonym('D')} to {pseudonym('E')}: link-hours 6," in printed
    out = tmp_path / "configs" / "x72y429"
    sites = gpd.read_parquet(out / "tx_sites_real.parquet")
    assert list(sites["site_id"]) == [pseudonym(name) for name in SITE_ORDER]
    assert list(sites["role"]) == SITE_ROLES
    assert list(gpd.read_parquet(out / "rx_real.parquet")["rx_id"]) == [pseudonym("A")]


def test_the_command_quotes_caseys_never_publish_sentence(
    tmp_path: Path, capture: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path) == 0
    assert (
        'Casey, issue #44, 2026-11-16: "Coverage outputs built from real gateway positions '
        'inherit the rule that precise MeshSTL positions are never published".'
    ) in capsys.readouterr().out


def test_the_counts_line_names_no_pseudonym(
    tmp_path: Path, capture: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path) == 0
    line = next(
        text for text in capsys.readouterr().out.splitlines() if text.startswith("counts for")
    )
    assert line == (
        "counts for issue #44: pairs 5 (one box per end 5, more than one box 0); pairs touching "
        "the tile 3 (both ends 1); nodes selected 4; extent pass 4, fail 0; pairs dropped 0 "
        "(straddles the tile edge 0, fails the DEM edge check 0, used more than one box in its "
        "kept hours 0)"
    )


def placed(name: str, role: str) -> PlacedNode:
    return PlacedNode(pseudonym(name), role, link_box(name), False)


def test_the_counts_line_carries_the_drop_counts_and_no_pseudonym() -> None:
    """A pair dropped for both reasons counts once in the total and once under each reason."""
    one = PairActivity(pseudonym("D"), pseudonym("E"), 1, 1, 0, 3)
    two = PairActivity(pseudonym("M"), pseudonym("E"), 1, 1, 0, 3)
    moved = MovedPair(pseudonym("B"), pseudonym("G2"), ("gateway",), 2)
    links = CaptureLinks((one, two), {}, 0, 0, (moved,))
    tile = {"both ends": 1, "only the gateway end": 2, "only the relayer end": 0, "touched": 3}
    selection = Selection(
        placed=(placed("G1", "both_ends_gateway"), placed("A", "both_ends_relayer")),
        dem_check={pseudonym("G1"): True, pseudonym("A"): False},
        dropped=(
            DroppedPair(one, OTHER_PAIR, (("relayer", STRADDLES),)),
            DroppedPair(two, OTHER_PAIR, (("relayer", STRADDLES), ("gateway", FAILS_DEM))),
            DroppedPair(moved, OTHER_PAIR, (("gateway", MORE_THAN_ONE_BOX),)),
        ),
    )
    line = engine_inputs.counts_line(links, tile, selection)
    assert line == (
        "counts for issue #44: pairs 3 (one box per end 2, more than one box 1); pairs touching "
        "the tile 3 (both ends 1); nodes selected 2; extent pass 1, fail 1; pairs dropped 3 "
        "(straddles the tile edge 2, fails the DEM edge check 1, used more than one box in its "
        "kept hours 1)"
    )
    assert not any(node.pseudonym in line for node in NODES.values())


def names(text: str) -> str:
    """The text with each {name} of a fixture node or partner (`{PG2}`) as its pseudonym."""
    partners = {node.name: node.pseudonym for node in PARTNERS.values()}
    return text.format(**{name: pseudonym(name) for name in NODES}, **partners)


SITE_RULE_LINE = "sites by tile membership:"


DEFAULT_TILE_LINE = (
    "tile x72y429: pairs with both ends 1, only the gateway end 2, only the relayer end 0, "
    "touched 3"
)


@pytest.mark.parametrize(
    ("options", "dem", "line", "before_selection"),
    [
        (
            {"g3_at_edge": True},
            DEM_BOUNDS_M,
            "dropped gateway-only pair {C} to {G3}: gateway straddles the tile edge",
            True,
        ),
        (
            {"moved_in_pair": True},
            DEM_BOUNDS_M,
            "dropped gateway-only pair {C} to {G3}: gateway used more than one box in its kept "
            "hours",
            True,
        ),
        (
            {},
            NARROW_DEM_BOUNDS_M,
            "dropped gateway-only pair {C} to {G3}: gateway fails the DEM edge check",
            False,
        ),
        (
            {},
            RECTANGLE_DEM_BOUNDS_M,
            "dropped gateway-only pair {C} to {G3}: gateway fails the DEM edge check",
            False,
        ),
    ],
)
def test_a_dropped_selected_pair_stops_the_run_before_any_write(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    options: dict[str, Any],
    dem: tuple[float, float, float, float],
    line: str,
    before_selection: bool,
) -> None:
    """The counts match, then a selected pair is dropped and the run stops, its line once.

    A straddle or two-box line prints before the selection's lines, an
    edge-check line after them. With `moved_in_pair` the counts check counts
    C to G3 before it is dropped (Casey, issue #55 comment 6985802330, item 3).
    """
    build_dem(tmp_path / "snapshots", bounds_m=dem)
    build_capture(tmp_path / "snapshots", **options)
    with capture_logs() as events:
        assert run(tmp_path) == 2
    assert not (tmp_path / "configs").exists()
    printed = capsys.readouterr().out
    lines = printed.splitlines()
    assert DEFAULT_TILE_LINE in lines
    assert [text for text in lines if text.startswith("dropped")] == [names(line)]
    rule = next(index for index, text in enumerate(lines) if text.startswith(SITE_RULE_LINE))
    assert (lines.index(names(line)) < rule) is before_selection
    if dem in (NARROW_DEM_BOUNDS_M, RECTANGLE_DEM_BOUNDS_M):
        pattern = rf"^{pseudonym('G3')} gateway_only_gateway: .*extent fail$"
        assert re.search(pattern, printed, re.M)
    assert "refused, nothing written: selected pairs dropped: gateway-only pair" in printed
    assert not any(text.startswith("counts for") for text in lines)
    assert coordinates_in(printed + repr(events)) == []


UNDETERMINED = "dropped pair (kind not determined) "


@pytest.mark.parametrize(
    ("straddler", "drop", "refusal", "tile_line"),
    [
        (
            "D",
            "{D} to {PD}: relayer straddles the tile edge",
            "refused, nothing written: pairs touching x72y429 are not 3",
            "tile x72y429: pairs with both ends 1, only the gateway end 2, only the relayer end 2, "
            "touched 5",
        ),
        (
            "B",
            "{B} to {PB}: relayer straddles the tile edge",
            "refused, nothing written: 2 pairs have both ends in x72y429, not one",
            "tile x72y429: pairs with both ends 2, only the gateway end 1, only the relayer end 1, "
            "touched 4",
        ),
        (
            "G2",
            "{PG2} to {G2}: gateway straddles the tile edge",
            "refused, nothing written: 3 pairs have only the gateway end in x72y429, not two",
            "tile x72y429: pairs with both ends 1, only the gateway end 3, only the relayer end 0, "
            "touched 4",
        ),
        (
            "A",
            "{A} to {PA}: relayer straddles the tile edge",
            "refused, nothing written: pairs touching x72y429 are not 3",
            "tile x72y429: pairs with both ends 1, only the gateway end 2, only the relayer end 1, "
            "touched 4",
        ),
    ],
)
def test_a_straddle_line_prints_before_a_tile_check_refusal(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    straddler: str,
    drop: str,
    refusal: str,
    tile_line: str,
) -> None:
    """Only the pair whose row straddles is dropped; its line prints before the refusal.

    The straddler's second place, in its second pair (`second_place`), lies
    across the tile edge; its own row with its first pair does not, so that
    pair stays (Casey, issue #55 comment 6985802330, item 6). Counted as
    touching in both pairs, the node fails a count check.
    """
    build_dem(tmp_path / "snapshots")
    build_capture(tmp_path / "snapshots", straddler=straddler)
    with capture_logs() as events:
        assert run(tmp_path, expect_pairs="6") == 2
    assert not (tmp_path / "configs").exists()
    printed = capsys.readouterr().out
    lines = printed.splitlines()
    assert tile_line in lines
    expected = names(UNDETERMINED + drop)
    assert [text for text in lines if text.startswith("dropped")] == [expected]
    assert lines.index(expected) < lines.index(refusal)
    assert not any(text.startswith(SITE_RULE_LINE) for text in lines)
    assert coordinates_in(printed + repr(events)) == []


def test_command_prints_and_logs_no_coordinate(
    tmp_path: Path, capture: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with capture_logs() as events:
        assert run(tmp_path) == 0
    assert coordinates_in(capsys.readouterr().out + repr(events)) == []


def test_a_refused_run_prints_and_logs_no_coordinate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build_dem(tmp_path / "snapshots")
    build_capture(tmp_path / "snapshots", moved_a=True)
    with capture_logs() as events:
        assert run(tmp_path, expect_pairs="6") == 2
    text = capsys.readouterr().out + repr(events)
    assert "refused, nothing written: pairs touching x72y429 are not 3" in text
    assert coordinates_in(text) == []


MOVED_C_G3 = (
    "dropped gateway-only pair {C} to {G3}: gateway used more than one box in its kept hours"
)


def test_a_kept_pair_with_another_box_in_hours_not_kept_is_reported_locally(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Casey, issue #55 comment 6985802330, item 2: kept, and reported to him in the session.

    C to G3's 14:00 hour, two receipts on a box 2 km west, is not kept, so the
    pair keeps G3's own box and the layers are written as by default. The
    per-pair line names pseudonyms and stays local; the counts line, which
    goes on issue #44, does not carry it.
    """
    build_dem(tmp_path / "snapshots")
    build_capture(tmp_path / "snapshots", moved_short_hour=True)
    with capture_logs() as events:
        assert run(tmp_path) == 0
    printed = capsys.readouterr().out
    lines = printed.splitlines()
    assert names("kept pair {C} to {G3}: gateway used another box in hours not kept") in lines
    assert any(text.startswith(f"{engine_inputs.OTHER_BOX_COUNT} 1 ") for text in lines)
    counts = next(text for text in lines if text.startswith("counts for"))
    assert "another box" not in counts
    sites = gpd.read_parquet(tmp_path / "configs" / "x72y429" / "tx_sites_real.parquet")
    assert list(sites["site_id"]) == [pseudonym(name) for name in SITE_ORDER]
    assert coordinates_in(printed + repr(events)) == []


def capture_with(*specs: tuple[str, str, int, float]) -> CaptureLinks:
    """Pairs (relayer, gateway, link-hours, the gateway's box shifted east in metres)."""

    def pair(relayer: str, gateway: str, hours: int, shift: float) -> PairActivity:
        boxes = cell_of(*NODES[relayer].box()), cell_of(*NODES[gateway].box(shift))
        return PairActivity(
            pseudonym(relayer), pseudonym(gateway), hours, hours, 0, 3 * hours, *boxes
        )

    pairs = tuple(pair(*spec) for spec in specs)
    return CaptureLinks(pairs, link_pairs._nodes(pairs), 0, 0)


# Reviewer reproductions, PR #70 comment 6989619559: (a) B's row with G1 lies
# outside the tile and its row with C inside; (b) G2's row with C lies 15 km east.
REPRODUCTION_A = (("B", "G1", 2, 0.0), ("C", "B", 4, -10_000.0), ("D", "G2", 1, 0.0))
REPRODUCTION_B = (("A", "G1", 2, 0.0), ("B", "G2", 5, 0.0), ("C", "G2", 4, 15_000.0))
KIND_LINE = (
    "kind differs, pair {{{r}}} to {{{g}}}: {by_rows} by its own rows, {counted} by the counts "
    "check; for Casey in the session (issue #55 comment 6990646633, item 3)"
)


def kind_line(relayer: str, gateway: str, by_rows: str, counted: str) -> str:
    return names(KIND_LINE.format(r=relayer, g=gateway, by_rows=by_rows, counted=counted))


NO_RECEIVER_LINE = (
    "no real receiver: this strict run produced no real receiver and wrote no rx_real.parquet, "
    "because no pair has both ends in the tile by its own rows; engine.prepare refuses this "
    "folder unless --no-real-receivers is passed (Casey, PR #70 comment 6992903491)"
)


def test_a_disagreement_with_no_receiver_row_writes_the_sites_and_no_receiver(
    tmp_path: Path,
    capture: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduction (a), Casey (PR #70 comment 6992903491), (b): do not stop.

    Kinds by rows leave no both-ends pair, so there is no receiver row. The
    strict run writes the sites and the local report and no rx_real.parquet
    at all; the disagreement and a plain no-receiver line are in the report
    and on the terminal, and neither is on the counts line.
    """
    monkeypatch.setattr(engine_inputs, "capture_links", lambda _rows: capture_with(*REPRODUCTION_A))
    with capture_logs() as events:
        assert run(tmp_path, expect_pairs="3") == 0
    out = tmp_path / "configs" / "x72y429"
    assert sorted(path.name for path in out.iterdir()) == [
        "engine_inputs_report.md",
        "tx_sites_real.parquet",
    ]
    sites = gpd.read_parquet(out / "tx_sites_real.parquet")
    assert set(sites["role"]) == {"gateway_only_gateway"}
    printed = capsys.readouterr().out
    lines = printed.splitlines()
    conflict = kind_line("B", "G1", "gateway-only pair", "both-ends pair")
    for line in (conflict, NO_RECEIVER_LINE):
        assert line in lines
        assert line in report_lines(tmp_path)
    assert "no real receiver" not in counts_for_issue(lines)
    assert_report(tmp_path, printed, heading="engine_inputs")
    assert coordinates_in(printed + repr(events)) == []


def test_engine_prepare_refuses_the_folder_without_no_real_receivers(
    tmp_path: Path, capture: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """engine.prepare's own main refuses a folder with no rx_real.parquet unless told otherwise."""
    monkeypatch.setattr(engine_inputs, "capture_links", lambda _rows: capture_with(*REPRODUCTION_A))
    assert run(tmp_path, expect_pairs="3") == 0
    folder = tmp_path / "configs" / "x72y429"
    assert not (folder / "rx_real.parquet").exists()
    monkeypatch.setenv("SNAPSHOTS_DIR", str(tmp_path / "snapshots"))
    monkeypatch.setattr(prepare_command, "configure_logging", lambda log_format: None)
    with capture_logs() as logs:
        assert prepare_command.main(["x72y429", "--folder", str(folder)]) == 2
    (refused,) = [entry for entry in logs if entry["log_level"] == "error"]
    assert "rx_real.parquet" in str(refused["reason"])


def test_a_disagreement_is_listed_in_a_strict_runs_report_and_the_run_goes_on(
    tmp_path: Path,
    capture: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduction (b), Casey (issue #55 comment 6990646633, item 3): listed, no stop.

    The line names pseudonyms, so it is in the terminal and the local report,
    and not in the counts line that goes on issue #44.
    """
    monkeypatch.setattr(engine_inputs, "capture_links", lambda _rows: capture_with(*REPRODUCTION_B))
    with capture_logs() as events:
        assert run(tmp_path, expect_pairs="3") == 0
    printed = capsys.readouterr().out
    lines = printed.splitlines()
    conflict = kind_line("C", "G2", "other pair", "gateway-only pair")
    assert conflict in lines
    assert conflict in report_lines(tmp_path)
    assert "kind differs" not in counts_for_issue(lines)
    sites = gpd.read_parquet(tmp_path / "configs" / "x72y429" / "tx_sites_real.parquet")
    assert list(sites["site_id"]) == [pseudonym("G1"), f"{pseudonym('G2')}.1"]
    assert_report(tmp_path, printed, heading="engine_inputs")
    assert coordinates_in(printed + repr(events)) == []


def test_a_strict_run_writes_its_local_report(
    tmp_path: Path, capture: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The strict run keeps its lines in engine_inputs_report.md, as a sites-only run does."""
    assert run(tmp_path) == 0
    printed = capsys.readouterr().out
    assert NO_RECEIVER_LINE not in printed
    assert_report(tmp_path, printed, heading="engine_inputs")
    assert report_lines(tmp_path)[-1] == counts_for_issue(printed.splitlines())


@pytest.mark.parametrize("option", ["nested_g1", "nested_g3", "ignored_a"])
def test_boxes_the_overlap_rule_reads_as_one_place_leave_the_layers_as_they_are(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], option: str
) -> None:
    """Each option adds boxes the rule of 2026-10-31 refused (lnk_02, Overlap amendment)."""
    build_dem(tmp_path / "snapshots")
    build_capture(tmp_path / "snapshots", **{option: True})
    with capture_logs() as events:
        assert run(tmp_path) == 0
    out = tmp_path / "configs" / "x72y429"
    sites = gpd.read_parquet(out / "tx_sites_real.parquet")
    receivers = gpd.read_parquet(out / "rx_real.parquet")
    assert list(sites["site_id"]) == [pseudonym(name) for name in SITE_ORDER]
    for frame, names_in_order in ((sites, SITE_ORDER), (receivers, ("A",))):
        assert_edges(frame, names_in_order)
    printed = capsys.readouterr().out
    assert not any(line.startswith("dropped") for line in printed.splitlines())
    assert coordinates_in(printed + repr(events)) == []


def test_command_refuses_an_unexpected_pair_count(tmp_path: Path, capture: Path) -> None:
    assert run(tmp_path, expect_pairs="8") == 2
    assert not (tmp_path / "configs").exists()


@pytest.mark.parametrize(
    "name", ["rx_real.parquet", "engine-inputs.partial", "engine_inputs_report.md"]
)
def test_command_refuses_to_overwrite(tmp_path: Path, capture: Path, name: str) -> None:
    out = tmp_path / "configs" / "x72y429"
    out.mkdir(parents=True)
    existing = out / name
    existing.write_bytes(b"earlier")
    assert run(tmp_path) == 2
    assert existing.read_bytes() == b"earlier"
    assert [path.name for path in out.iterdir()] == [name]


@pytest.mark.parametrize("flags", [(), ("--sites-only",)])
def test_command_refuses_an_out_folder_inside_a_checkout(
    tmp_path: Path, capture: Path, flags: tuple[str, ...]
) -> None:
    """The layers and the sites-only report hold real positions or pseudonyms; none enters git."""
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    out = checkout / "configs"
    assert run(tmp_path, out=out, flags=flags) == 2
    assert not out.exists()


def test_command_refuses_a_changed_snapshot(tmp_path: Path, capture: Path) -> None:
    hour_file = sorted((capture / "mqtt_message").glob("*.parquet"))[0]
    hour_file.write_bytes(hour_file.read_bytes() + b"\0")
    assert run(tmp_path) == 2


@pytest.mark.parametrize(
    "brokers",
    [("mqtt.meshtastic.org",), ("mqtt.meshstl.org",), None],
    ids=["public", "meshstl", "none"],
)
@pytest.mark.parametrize("flags", [(), ("--sites-only",)])
def test_command_refuses_a_snapshot_that_does_not_merge_both_brokers(
    tmp_path: Path, brokers: tuple[str, ...] | None, flags: tuple[str, ...]
) -> None:
    """Item 7's MeshSTL flag reads every copy in any snapshot; the command reads one.

    Casey, issue #55 comment 7016583529, item 4.
    """
    build_dem(tmp_path / "snapshots")
    build_capture(tmp_path / "snapshots", brokers=brokers)
    assert run(tmp_path, flags=flags) == 2
    assert not (tmp_path / "configs").exists()


def test_command_refuses_a_missing_dem_snapshot(tmp_path: Path) -> None:
    build_capture(tmp_path / "snapshots")
    assert run(tmp_path) == 2
    assert not (tmp_path / "configs").exists()


def test_a_failed_second_layer_leaves_nothing(
    tmp_path: Path, capture: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write = engine_inputs._write
    calls: list[Path] = []

    def fail_second(path: Path, frame: Any) -> None:
        calls.append(path)
        if len(calls) == 2:
            raise OSError("disk full")
        write(path, frame)

    monkeypatch.setattr(engine_inputs, "_write", fail_second)
    with pytest.raises(OSError, match="disk full"):
        run(tmp_path)
    assert not (tmp_path / "configs").exists()


def test_a_failed_move_leaves_nothing(
    tmp_path: Path, capture: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def move_one_then_fail(staging: Path, targets: list[Path], published: list[Path]) -> None:
        (staging / targets[0].name).rename(targets[0])
        published.append(targets[0])
        raise OSError("rename refused")

    monkeypatch.setattr(engine_inputs, "_publish", move_one_then_fail)
    with pytest.raises(OSError, match="rename refused"):
        run(tmp_path)
    assert not (tmp_path / "configs").exists()


def test_a_failure_keeps_an_output_folder_that_existed(
    tmp_path: Path, capture: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "configs" / "x72y429"
    out.mkdir(parents=True)
    (out / "x72y429.yaml").write_text("other agent's file", encoding="utf-8")

    def refuse(staging: Path, targets: list[Path], published: list[Path]) -> None:
        raise OSError("rename refused")

    monkeypatch.setattr(engine_inputs, "_publish", refuse)
    with pytest.raises(OSError):
        run(tmp_path)
    assert [path.name for path in out.iterdir()] == ["x72y429.yaml"]


# --sites-only: the x72y429 run with its real sites and no real receiver
# (Casey, 2026-11-17, engine_inputs (c)).

# A DEM 10 m square, far from every fixture node, so every gateway fails the edge check.
FAR_DEM_BOUNDS_M = (700_000.0, 4_250_000.0, 700_010.0, 4_250_010.0)
COUNTS_PASS = "counts check against 2026-11-08: pass"
NO_RECEIVER = "no real receiver written; relayers not DEM-checked"


def run_sites_only(tmp_path: Path, expect_pairs: str = "5", tile: str = "x72y429") -> int:
    argv = ["--snapshot", SNAPSHOT_ID, "--snapshots-dir", str(tmp_path / "snapshots")]
    argv += ["--tile", tile, "--dem-snapshot", DEM_SNAPSHOT_ID]
    argv += ["--out", str(tmp_path / "configs" / "x72y429"), "--expect-pairs", expect_pairs]
    return engine_inputs.main([*argv, "--sites-only"])


def counts_for_issue(lines: list[str]) -> str:
    return next(text for text in lines if text.startswith("counts for"))


REPORT_PREFIXES = (
    "data-quality",
    "no real receiver",
    "kind differs",
    "pairs kept with another box",
    "kept pair",
    "dropped",
    "counts check",
    "sites by",
    "sites only",
    "left out",
    "counts for",
)
DATA_QUALITY = (
    "data-quality counts: lnk_01_direct_hop_contradicted {count} (lnk_01, a direct hop the "
    "gateway's NeighborInfo contradicts, dropped; Casey, PR #66, Q4 (a)); one_box_dropped_hours "
    "0 (lnk_02)"
)
GATEWAY_LINE = re.compile(r"[0-9a-f]{64}(\.\d+)? (both_ends|gateway_only)_(gateway|relayer): ")


def recorded(lines: list[str]) -> list[str]:
    """The terminal lines the local report must hold, in the order they printed."""
    return [t for t in lines if t.startswith(REPORT_PREFIXES) or GATEWAY_LINE.match(t)]


def report_text(tmp_path: Path) -> str:
    path = tmp_path / "configs" / "x72y429" / "engine_inputs_report.md"
    return path.read_text(encoding="utf-8")


def report_lines(tmp_path: Path) -> list[str]:
    """The lines inside the report's text block."""
    text = report_text(tmp_path)
    block = text.split("```text\n", 1)[1].split("\n```", 1)[0]
    return block.splitlines()


def assert_report(
    tmp_path: Path, printed: str, heading: str = "engine_inputs --sites-only"
) -> None:
    """The report holds every recorded terminal line and no coordinate (Casey, issue #55, 7)."""
    text = report_text(tmp_path)
    assert text.startswith(f"# {heading}, x72y429\n")
    assert engine_inputs.REPORT_NOTE in text
    assert f"Snapshot {SNAPSHOT_ID}, DEM snapshot {DEM_SNAPSHOT_ID}." in text
    assert report_lines(tmp_path) == recorded(printed.splitlines())
    assert coordinates_in(text) == []


def assert_sites(tmp_path: Path, kept: tuple[str, ...]) -> None:
    """The site layer and the local report alone, the kept gateways in site order, marked real."""
    out = tmp_path / "configs" / "x72y429"
    assert sorted(path.name for path in out.iterdir()) == [
        "engine_inputs_report.md",
        "tx_sites_real.parquet",
    ]
    sites = gpd.read_parquet(out / "tx_sites_real.parquet")
    assert list(sites.columns) == ["site_id", "geometry", *EDGE_COLUMNS, "role"]
    assert sites.crs.to_epsg() == 26915
    roles = dict(zip(SITE_ORDER, SITE_ROLES, strict=True))
    assert list(sites["site_id"]) == [pseudonym(name) for name in kept]
    assert list(sites["role"]) == [roles[name] for name in kept]
    assert_edges(sites, kept)
    metadata = pq.read_metadata(out / "tx_sites_real.parquet").metadata
    assert metadata[b"mesh_terrain:positions"] == b"real"


def test_sites_only_is_refused_on_another_tile(
    tmp_path: Path, capture: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Casey's decision is for the x72y429 run; corridor runs keep the strict checks."""
    assert run_sites_only(tmp_path, tile="x71y428") == 2
    assert not (tmp_path / "configs").exists()
    lines = capsys.readouterr().out.splitlines()
    assert (
        "refused, nothing written: --sites-only is accepted only with --tile x72y429 "
        "(Casey, 2026-11-17: this run only; corridor runs keep the strict checks)"
    ) in lines
    assert not any(text.startswith("snapshot ") for text in lines)


def test_sites_only_writes_the_sites_and_no_receiver(
    tmp_path: Path, capture: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with capture_logs() as events:
        assert run_sites_only(tmp_path) == 0
    assert_sites(tmp_path, SITE_ORDER)
    printed = capsys.readouterr().out
    lines = printed.splitlines()
    assert COUNTS_PASS in lines
    assert engine_inputs.SITES_ONLY_RULE in lines
    assert (
        "wrote tx_sites_real.parquet (3 sites) and engine_inputs_report.md (local only); "
        "no rx_real.parquet: no real receiver written (--sites-only)"
    ) in lines
    assert_report(tmp_path, printed)
    assert report_lines(tmp_path)[0] == DATA_QUALITY.format(count=0)
    assert report_lines(tmp_path)[-1] == counts_for_issue(lines)
    assert counts_for_issue(lines) == (
        "counts for issue #44: pairs 5 (one box per end 5, more than one box 0); pairs touching "
        "the tile 3 (both ends 1); nodes selected 3; extent pass 3, fail 0; pairs dropped 0 "
        "(straddles the tile edge 0, fails the DEM edge check 0, used more than one box in its "
        "kept hours 0); sites written 3, left out 0; no real receiver written; relayers not "
        "DEM-checked"
    )
    assert "both_ends_relayer" not in printed
    assert not any(text.startswith(("dropped", "left out")) for text in lines)
    assert coordinates_in(printed + repr(events)) == []


def test_sites_only_writes_the_gateway_of_a_both_ends_pair_its_relayer_drops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A's row with its partner straddles, so only that pair is dropped; A to G1 stays."""
    build_dem(tmp_path / "snapshots")
    build_capture(tmp_path / "snapshots", straddler="A")
    with capture_logs() as events:
        assert run_sites_only(tmp_path) == 0
    assert_sites(tmp_path, SITE_ORDER)
    printed = capsys.readouterr().out
    lines = printed.splitlines()
    assert [text for text in lines if text.startswith("dropped")] == [
        names("dropped other pair {A} to {PA}: relayer straddles the tile edge"),
    ]
    assert not any(text.startswith("left out") for text in lines)
    assert counts_for_issue(lines).endswith(
        "pairs dropped 1 (straddles the tile edge 1, fails the DEM edge check 0, used more than "
        f"one box in its kept hours 0); sites written 3, left out 0; {NO_RECEIVER}"
    )
    assert_report(tmp_path, printed)
    assert coordinates_in(printed + repr(events)) == []


@pytest.mark.parametrize(
    ("options", "dem", "left_out", "kept", "dropped", "counts"),
    [
        (
            {"g3_at_edge": True},
            DEM_BOUNDS_M,
            ["left out {G3} gateway_only_gateway: straddles the tile edge"],
            ("G1", "G2"),
            ["dropped gateway-only pair {C} to {G3}: gateway straddles the tile edge"],
            "nodes selected 2; extent pass 2, fail 0; pairs dropped 1 (straddles the tile "
            "edge 1, fails the DEM edge check 0, used more than one box in its kept hours 0); "
            "sites written 2, left out 1",
        ),
        (
            {},
            NARROW_DEM_BOUNDS_M,
            ["left out {G3} gateway_only_gateway: fails the DEM edge check"],
            ("G1", "G2"),
            ["dropped gateway-only pair {C} to {G3}: gateway fails the DEM edge check"],
            "nodes selected 3; extent pass 2, fail 1; pairs dropped 1 (straddles the tile "
            "edge 0, fails the DEM edge check 1, used more than one box in its kept hours 0); "
            "sites written 2, left out 1",
        ),
        (
            {},
            RECTANGLE_DEM_BOUNDS_M,
            ["left out {G3} gateway_only_gateway: fails the DEM edge check"],
            ("G1", "G2"),
            ["dropped gateway-only pair {C} to {G3}: gateway fails the DEM edge check"],
            "nodes selected 3; extent pass 2, fail 1; pairs dropped 1 (straddles the tile "
            "edge 0, fails the DEM edge check 1, used more than one box in its kept hours 0); "
            "sites written 2, left out 1",
        ),
    ],
)
def test_sites_only_leaves_out_a_gateway_with_its_reason(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    options: dict[str, Any],
    dem: tuple[float, float, float, float],
    left_out: list[str],
    kept: tuple[str, ...],
    dropped: list[str],
    counts: str,
) -> None:
    build_dem(tmp_path / "snapshots", bounds_m=dem)
    build_capture(tmp_path / "snapshots", **options)
    with capture_logs() as events:
        assert run_sites_only(tmp_path) == 0
    assert_sites(tmp_path, kept)
    printed = capsys.readouterr().out
    lines = printed.splitlines()
    assert [text for text in lines if text.startswith("left out")] == [names(t) for t in left_out]
    assert [text for text in lines if text.startswith("dropped")] == [names(t) for t in dropped]
    line = counts_for_issue(lines)
    assert line.endswith(f"{counts}; {NO_RECEIVER}")
    assert not any(node.pseudonym in line for node in NODES.values())
    assert_report(tmp_path, printed)
    assert all(names(text) in report_lines(tmp_path) for text in left_out)
    assert coordinates_in(printed + repr(events)) == []


def test_sites_only_keeps_a_gateways_inside_row_and_leaves_out_its_straddling_row(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Casey, issue #55 comment 6985802330, item 6, end to end.

    G2's row with B lies inside the tile; its row with its partner crosses the
    west edge. Only the partner's pair is dropped, and G2 is written at its
    own box as `<G2>.2` (the partner's pair sorts first, so its row is `.1`).
    """
    build_dem(tmp_path / "snapshots")
    build_capture(tmp_path / "snapshots", straddler="G2")
    with capture_logs() as events:
        assert run_sites_only(tmp_path, expect_pairs="6") == 0
    g2 = pseudonym("G2")
    sites = gpd.read_parquet(tmp_path / "configs" / "x72y429" / "tx_sites_real.parquet")
    assert list(sites["site_id"]) == [pseudonym("G1"), pseudonym("G3"), f"{g2}.2"]
    point = sites.geometry.iloc[2]
    assert (point.x, point.y) == pytest.approx(centre_m("G2"), abs=1e-3)
    printed = capsys.readouterr().out
    lines = printed.splitlines()
    assert [text for text in lines if text.startswith("dropped")] == [
        names("dropped gateway-only pair {PG2} to {G2}: gateway straddles the tile edge")
    ]
    assert [text for text in lines if text.startswith("left out")] == [
        f"left out {g2}.1 gateway_only_gateway: straddles the tile edge"
    ]
    assert_report(tmp_path, printed)
    assert coordinates_in(printed + repr(events)) == []


def test_sites_only_places_a_gateway_at_two_places_once_per_pair(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Casey, issue #44, 2026-11-17: "a moving node is placed per pair".

    G3 used its own box with C and a box 2 km west with its partner, both
    inside the tile: two rows, `<G3>.1` and `<G3>.2`, numbered in the capture's
    pair order, each at the box its pair used.
    """
    build_dem(tmp_path / "snapshots")
    build_capture(tmp_path / "snapshots", moved_g3=True)
    with capture_logs() as events:
        assert run_sites_only(tmp_path, expect_pairs="6") == 0
    layer = tmp_path / "configs" / "x72y429" / "tx_sites_real.parquet"
    sites = gpd.read_parquet(layer)
    g3 = pseudonym("G3")
    # engine.prepare's own reader takes the rows: distinct ids that can name a coverage file.
    endpoints, roles = prepare_command._read_sites(layer)
    assert [endpoint.endpoint_id for endpoint in endpoints] == list(sites["site_id"])
    assert set(roles.values()) == {"both_ends_gateway", "gateway_only_gateway"}
    assert list(sites["site_id"]) == [pseudonym("G1"), f"{g3}.1", pseudonym("G2"), f"{g3}.2"]
    assert list(sites["role"]) == ["both_ends_gateway", *["gateway_only_gateway"] * 3]
    places = {
        f"{g3}.1": box_centre_m(NODES["G3"].box()),
        f"{g3}.2": box_centre_m(NODES["G3"].box(G3_MOVED_SHIFT_M)),
    }
    for site_id, point in zip(sites["site_id"], sites.geometry, strict=True):
        if site_id in places:
            assert (point.x, point.y) == pytest.approx(places[site_id], abs=1e-3)
    printed = capsys.readouterr().out
    assert not any(text.startswith(("left out", "dropped")) for text in printed.splitlines())
    assert_report(tmp_path, printed)
    assert coordinates_in(printed + repr(events)) == []


D_STRADDLES = ["dropped other pair {D} to {PD}: relayer straddles the tile edge"]


@pytest.mark.parametrize(
    ("options", "expect_pairs", "differences", "dropped"),
    [
        ({}, "8", "5 pairs, not the 8 expected", []),
        ({"straddler": "D"}, "6", "pairs touching x72y429 are not 3", D_STRADDLES),
        (
            {"straddler": "D"},
            "8",
            "6 pairs, not the 8 expected; pairs touching x72y429 are not 3",
            D_STRADDLES,
        ),
    ],
)
def test_sites_only_reports_a_counts_mismatch_and_does_not_refuse(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    options: dict[str, Any],
    expect_pairs: str,
    differences: str,
    dropped: list[str],
) -> None:
    """Every count that differs is named on one line; the sites are written all the same.

    A straddle drop line names its pair's kind even though the counts differ,
    since a sites-only run never refuses on them.
    """
    build_dem(tmp_path / "snapshots")
    build_capture(tmp_path / "snapshots", **options)
    with capture_logs() as events:
        assert run_sites_only(tmp_path, expect_pairs=expect_pairs) == 0
    assert_sites(tmp_path, SITE_ORDER)
    printed = capsys.readouterr().out
    lines = printed.splitlines()
    assert (
        f"counts check against 2026-11-08: differs ({differences}); --sites-only reports it "
        "and does not refuse"
    ) in lines
    assert [text for text in lines if text.startswith("dropped")] == [names(t) for t in dropped]
    assert_report(tmp_path, printed)
    assert coordinates_in(printed + repr(events)) == []


def test_sites_only_roles_follow_each_pairs_own_rows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """B's row with its partner straddles, so B counts as touching for the counts check.

    The counts then read B to G2 as a both-ends pair, but B's row in that pair
    lies outside the tile, so by its own rows B to G2 is a gateway-only pair
    and G2 is written as `gateway_only_gateway` (Casey, issue #55 comment
    6985802330, item 6: each pair is judged by its own row). Only B's pair
    with its partner is dropped.
    """
    build_dem(tmp_path / "snapshots")
    build_capture(tmp_path / "snapshots", straddler="B")
    with capture_logs() as events:
        assert run_sites_only(tmp_path, expect_pairs="6") == 0
    assert_sites(tmp_path, SITE_ORDER)
    printed = capsys.readouterr().out
    lines = printed.splitlines()
    assert (
        "counts check against 2026-11-08: differs (2 pairs have both ends in x72y429, not one; "
        "1 pairs have only the gateway end in x72y429, not two; pairs touching x72y429 are not "
        "3); --sites-only reports it and does not refuse"
    ) in lines
    assert [text for text in lines if text.startswith("dropped")] == [
        names("dropped other pair {B} to {PB}: relayer straddles the tile edge"),
    ]
    # Casey (issue #55 comment 6990646633, item 3): the pair's own rows decide;
    # the disagreement is listed and the run goes on.
    conflict = names(
        "kind differs, pair {B} to {G2}: gateway-only pair by its own rows, both-ends pair by the "
        "counts check; for Casey in the session (issue #55 comment 6990646633, item 3)"
    )
    assert [text for text in lines if text.startswith("kind differs")] == [conflict]
    assert conflict in report_lines(tmp_path)
    assert counts_for_issue(lines).endswith(f"sites written 3, left out 0; {NO_RECEIVER}")
    assert_report(tmp_path, printed)
    assert coordinates_in(printed + repr(events)) == []


def test_sites_only_names_a_tile_with_no_gateway_in_it() -> None:
    """No candidate at all is not the same as every candidate left out."""
    chosen = SiteSelection(Selection(placed=(), dem_check={}, dropped=()), left_out=())
    message = r"^no site: no pair touching the tile has its gateway in it$"
    with pytest.raises(SelectionError, match=message):
        chosen.sites()


def test_sites_only_refuses_when_no_site_is_left(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    build_dem(tmp_path / "snapshots", bounds_m=FAR_DEM_BOUNDS_M)
    build_capture(tmp_path / "snapshots")
    with capture_logs() as events:
        assert run_sites_only(tmp_path) == 2
    assert not (tmp_path / "configs").exists()
    printed = capsys.readouterr().out
    lines = printed.splitlines()
    assert [text for text in lines if text.startswith("left out")] == [
        names(f"left out {{{name}}} {role}: fails the DEM edge check")
        for name, role in zip(SITE_ORDER, SITE_ROLES, strict=True)
    ]
    refusal = "refused, nothing written: no site left; all 3 in-tile gateways were left out"
    assert refusal in lines
    assert not any(text.startswith("counts for") for text in lines)
    assert coordinates_in(printed + repr(events)) == []


def test_sites_only_does_not_place_the_relayer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A moved inside the tile: the strict run refuses on it, the site layer does not need it."""
    build_dem(tmp_path / "snapshots")
    build_capture(tmp_path / "snapshots", moved_a=True)
    assert run_sites_only(tmp_path) == 0
    assert_sites(tmp_path, SITE_ORDER)
    printed = capsys.readouterr().out
    assert pseudonym("A") + " both_ends_relayer" not in printed
    assert_report(tmp_path, printed)


@pytest.mark.parametrize(
    "name",
    [
        "rx_real.parquet",
        "tx_sites_real.parquet",
        "engine-inputs.partial",
        "engine_inputs_report.md",
    ],
)
def test_sites_only_refuses_an_existing_layer(tmp_path: Path, capture: Path, name: str) -> None:
    """An earlier rx_real.parquet would sit beside the new sites; engine.prepare needs it absent.

    An earlier report is refused too, so a report always belongs to the layer beside it.
    """
    out = tmp_path / "configs" / "x72y429"
    out.mkdir(parents=True)
    (out / name).write_bytes(b"earlier")
    assert run_sites_only(tmp_path) == 2
    assert [path.name for path in out.iterdir()] == [name]
    assert (out / name).read_bytes() == b"earlier"


def test_a_failed_sites_only_move_leaves_neither_the_layer_nor_the_report(
    tmp_path: Path, capture: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def move_one_then_fail(staging: Path, targets: list[Path], published: list[Path]) -> None:
        (staging / targets[0].name).rename(targets[0])
        published.append(targets[0])
        raise OSError("rename refused")

    monkeypatch.setattr(engine_inputs, "_publish", move_one_then_fail)
    with pytest.raises(OSError, match="rename refused"):
        run_sites_only(tmp_path)
    assert not (tmp_path / "configs").exists()


def test_without_sites_only_a_moved_gateway_still_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """G3's second place comes with a third gateway-only pair, so the strict run refuses."""
    build_dem(tmp_path / "snapshots")
    build_capture(tmp_path / "snapshots", moved_g3=True)
    assert run(tmp_path, expect_pairs="6") == 2
    assert not (tmp_path / "configs").exists()
    assert (
        "refused, nothing written: 3 pairs have only the gateway end in x72y429, not two"
    ) in capsys.readouterr().out.splitlines()


def test_sites_only_reports_a_pair_dropped_for_two_boxes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """C to G3 is dropped before the tile, so G3 is no candidate; the drop is in the report."""
    build_dem(tmp_path / "snapshots")
    build_capture(tmp_path / "snapshots", moved_in_pair=True)
    with capture_logs() as events:
        assert run_sites_only(tmp_path) == 0
    assert_sites(tmp_path, ("G1", "G2"))
    printed = capsys.readouterr().out
    assert names(MOVED_C_G3) in report_lines(tmp_path)
    assert counts_for_issue(printed.splitlines()).startswith(
        "counts for issue #44: pairs 5 (one box per end 4, more than one box 1);"
    )
    assert_report(tmp_path, printed)
    assert coordinates_in(printed + repr(events)) == []


def test_sites_only_reports_the_direct_hops_neighbor_info_contradicts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Casey, PR #66, Q4 (a): D's three direct hops to E are dropped and counted in the report."""
    build_dem(tmp_path / "snapshots")
    build_capture(tmp_path / "snapshots", contradicted_d=True)
    with capture_logs() as events:
        assert run_sites_only(tmp_path, expect_pairs="4") == 0
    assert_sites(tmp_path, SITE_ORDER)
    printed = capsys.readouterr().out
    assert report_lines(tmp_path)[0] == DATA_QUALITY.format(count=3)
    assert COUNTS_PASS.replace("pass", "differs") not in printed
    assert_report(tmp_path, printed)
    assert coordinates_in(printed + repr(events)) == []


def test_engine_prepare_accepts_the_written_folder_and_the_engine_admits_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The layers pass engine.prepare end to end, and its outputs reach the engine (ADR 0008).

    prepare reads the tile's own DEM and DSM ids, written here as a flat
    made-up 10 m raster over the tile and beyond it.
    """
    snapshots = tmp_path / "snapshots"
    build_dem(snapshots)
    build_capture(snapshots)
    assert run(tmp_path) == 0
    heights_m = np.full((1200, 1200), 150.0, dtype=np.float32)
    corner = (719_000.0, 4_291_000.0)
    ids = TILE_SNAPSHOTS["x72y429"]
    write_raster_snapshot(snapshots, ids["dem"], {"dem.tif": (heights_m, 10.0, corner)})
    write_raster_snapshot(snapshots, ids["dsm"], {"dsm.tif": (heights_m + 5.0, 10.0, corner)})
    folder = tmp_path / "configs" / "x72y429"
    monkeypatch.setenv("SNAPSHOTS_DIR", str(snapshots))
    monkeypatch.setattr(prepare_command, "configure_logging", lambda log_format: None)
    assert prepare_command.main(["x72y429", "--folder", str(folder)]) == 0
    assert (folder / STAMP_FILE).exists()
    for name, id_column, real in (
        ("tx_sites.parquet", "site_id", SITE_ORDER),
        ("rx_points.parquet", "rx_id", ("A",)),
    ):
        require_admissible_layer(folder / name)
        boxes = {
            endpoint.endpoint_id: endpoint.box for endpoint in read_points(folder / name, id_column)
        }
        for node in real:
            assert boxes[pseudonym(node)] == edge_box(node)
