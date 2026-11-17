"""The engine input command: real site and receiver layers from a capture snapshot.

Every test runs on the synthetic capture and DEM in tests/engine_inputs_fixture.py;
none reads the real snapshot.
"""

import math
import re
from pathlib import Path
from typing import Any

import geopandas as gpd
import pyarrow.parquet as pq
import pytest
from engine_fixture import no_logger_caching
from engine_inputs_fixture import (
    DEM_SNAPSHOT_ID,
    NODES,
    SNAPSHOT_ID,
    box_centre_m,
    build_capture,
    build_dem,
    centre_m,
    every_box,
)
from structlog.testing import capture_logs

from engine import positions_marker
from engine.points import read_points
from ingester import engine_inputs
from ingester.link_pairs import CaptureLinks, PairActivity, SelectionError
from ingester.tile_selection import (
    FAILS_DEM,
    OTHER_PAIR,
    STRADDLES,
    DroppedPair,
    PlacedNode,
    Selection,
    SiteSelection,
)

# The import above activates the autouse fixture for this module.
_ = no_logger_caching


def pseudonym(name: str) -> str:
    return NODES[name].pseudonym


# The both-ends gateway, then the gateway-only pairs' gateways in the capture's
# pair order, which sorts by relayer pseudonym: C to G3 before B to G2.
SITE_ORDER = ("G1", "G3", "G2")
SITE_ROLES = ["both_ends_gateway", "gateway_only_gateway", "gateway_only_gateway"]


def east_west_half_m(name: str) -> float:
    """The east-west half of a node's box: Casey's inscribed square (issue #44, 1 (b))."""
    latitude_i, _, bits = NODES[name].box()
    return 2 ** (31 - bits) * 1e-7 * 111_320.0 * math.cos(math.radians(latitude_i / 1e7))


def run(tmp_path: Path, expect_pairs: str = "5", out: Path | None = None) -> int:
    out = out or tmp_path / "configs" / "x72y429"
    argv = ["--snapshot", SNAPSHOT_ID, "--snapshots-dir", str(tmp_path / "snapshots")]
    argv += ["--tile", "x72y429", "--dem-snapshot", DEM_SNAPSHOT_ID, "--out", str(out)]
    return engine_inputs.main([*argv, "--expect-pairs", expect_pairs])


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
        "rx_real.parquet",
        "tx_sites_real.parquet",
    ]
    sites = gpd.read_parquet(out / "tx_sites_real.parquet")
    receivers = gpd.read_parquet(out / "rx_real.parquet")
    assert list(sites.columns) == ["site_id", "geometry", "box_half_width_m", "role"]
    assert list(receivers.columns) == ["rx_id", "geometry", "box_half_width_m"]
    assert sites.crs.to_epsg() == receivers.crs.to_epsg() == 26915
    assert list(sites["site_id"]) == [pseudonym(name) for name in SITE_ORDER]
    assert list(sites["role"]) == SITE_ROLES
    assert list(receivers["rx_id"]) == [pseudonym("A")]
    for frame, column in ((sites, "site_id"), (receivers, "rx_id")):
        for row_id, point in zip(frame[column], frame.geometry, strict=True):
            name = next(key for key, node in NODES.items() if node.pseudonym == row_id)
            assert (point.x, point.y) == pytest.approx(centre_m(name), abs=1e-6)
    assert str(sites["box_half_width_m"].dtype) == "float64"
    assert list(sites["box_half_width_m"]) == pytest.approx(
        [east_west_half_m(name) for name in SITE_ORDER], abs=1e-9
    )
    assert list(receivers["box_half_width_m"]) == pytest.approx([east_west_half_m("A")], abs=1e-9)
    assert len(read_points(out / "tx_sites_real.parquet", "site_id")) == 3
    assert len(read_points(out / "rx_real.parquet", "rx_id")) == 1
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
    assert f"pair {pseudonym('D')} to {pseudonym('E')}: link-hours over both runs 6," in printed
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
        "counts for issue #44: pairs public 4, MeshSTL 2, shared 1, union 5; pairs touching "
        "the tile 3 (both ends 1); nodes selected 4; extent pass 4, fail 0; pairs dropped 0 "
        "(straddles the tile edge 0, fails the DEM edge check 0)"
    )


def placed(name: str, role: str) -> PlacedNode:
    return PlacedNode(pseudonym(name), role, *centre_m(name), east_west_half_m(name), False)


def test_the_counts_line_carries_the_drop_counts_and_no_pseudonym() -> None:
    """A pair dropped for both reasons counts once in the total and once under each reason."""
    links = CaptureLinks((), {"public": 4, "meshstl": 2, "shared": 1, "union": 5}, {}, 0, 0)
    tile = {"both ends": 1, "only the gateway end": 2, "only the relayer end": 0, "touched": 3}
    one = PairActivity(pseudonym("D"), pseudonym("E"), 1, 0, 1, 3)
    two = PairActivity(pseudonym("M"), pseudonym("E"), 1, 0, 1, 3)
    selection = Selection(
        placed=(placed("G1", "both_ends_gateway"), placed("A", "both_ends_relayer")),
        dem_check={pseudonym("G1"): True, pseudonym("A"): False},
        dropped=(
            DroppedPair(one, OTHER_PAIR, (("relayer", STRADDLES),)),
            DroppedPair(two, OTHER_PAIR, (("relayer", STRADDLES), ("gateway", FAILS_DEM))),
        ),
    )
    line = engine_inputs.counts_line(links, tile, selection)
    assert line == (
        "counts for issue #44: pairs public 4, MeshSTL 2, shared 1, union 5; pairs touching "
        "the tile 3 (both ends 1); nodes selected 2; extent pass 1, fail 1; pairs dropped 2 "
        "(straddles the tile edge 2, fails the DEM edge check 1)"
    )
    assert not any(node.pseudonym in line for node in NODES.values())


def names(text: str) -> str:
    return text.format(**{name: pseudonym(name) for name in NODES})


SITE_RULE_LINE = "sites by tile membership:"


@pytest.mark.parametrize(
    ("options", "line", "before_selection"),
    [
        (
            {"straddler": "G2"},
            "dropped gateway-only pair {B} to {G2}: gateway straddles the tile edge",
            True,
        ),
        (
            {"straddler": "A"},
            "dropped both-ends pair {A} to {G1}: relayer straddles the tile edge",
            True,
        ),
        (
            {"g3_at_edge": True},
            "dropped gateway-only pair {C} to {G3}: gateway fails the DEM edge check",
            False,
        ),
    ],
)
def test_a_dropped_selected_pair_stops_the_run_before_any_write(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    options: dict[str, Any],
    line: str,
    before_selection: bool,
) -> None:
    """A straddle line prints before the selection's lines, an edge-check line after them, once."""
    build_dem(tmp_path / "snapshots")
    build_capture(tmp_path / "snapshots", **options)
    with capture_logs() as events:
        assert run(tmp_path) == 2
    assert not (tmp_path / "configs").exists()
    printed = capsys.readouterr().out
    lines = printed.splitlines()
    assert [text for text in lines if text.startswith("dropped")] == [names(line)]
    rule = next(index for index, text in enumerate(lines) if text.startswith(SITE_RULE_LINE))
    assert (lines.index(names(line)) < rule) is before_selection
    if options.get("g3_at_edge"):
        pattern = rf"^{pseudonym('G3')} gateway_only_gateway: .*extent fail$"
        assert re.search(pattern, printed, re.M)
    assert "refused, nothing written: selected pairs dropped" in printed
    assert not any(text.startswith("counts for") for text in lines)
    assert coordinates_in(printed + repr(events)) == []


@pytest.mark.parametrize(
    ("straddler", "line", "refusal", "tile_line"),
    [
        (
            "D",
            "dropped pair (kind not determined) {D} to {E}: relayer straddles the tile edge",
            "refused, nothing written: pairs touching x72y429 are not 3",
            "tile x72y429: pairs with both ends 1, only the gateway end 2, only the relayer end 1, "
            "touched 4",
        ),
        (
            "B",
            "dropped pair (kind not determined) {B} to {G2}: relayer straddles the tile edge",
            "refused, nothing written: 2 pairs have both ends in x72y429, not one",
            "tile x72y429: pairs with both ends 2, only the gateway end 1, only the relayer end 0, "
            "touched 3",
        ),
    ],
)
def test_a_straddle_line_prints_before_a_tile_check_refusal(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    straddler: str,
    line: str,
    refusal: str,
    tile_line: str,
) -> None:
    """Counted as in the tile, the node fails a count check; its drop line comes first."""
    build_dem(tmp_path / "snapshots")
    build_capture(tmp_path / "snapshots", straddler=straddler)
    with capture_logs() as events:
        assert run(tmp_path) == 2
    assert not (tmp_path / "configs").exists()
    printed = capsys.readouterr().out
    lines = printed.splitlines()
    assert tile_line in lines
    assert [text for text in lines if text.startswith("dropped")] == [names(line)]
    assert lines.index(names(line)) < lines.index(refusal)
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
        assert run(tmp_path) == 2
    text = capsys.readouterr().out + repr(events)
    assert "refused, nothing written: the both_ends_relayer's 2 usable boxes" in text
    assert coordinates_in(text) == []


def test_command_refuses_a_node_that_moved_during_the_capture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both of A's boxes lie inside the tile: it does not straddle, so no drop rule applies."""
    build_dem(tmp_path / "snapshots")
    build_capture(tmp_path / "snapshots", moved_a=True)
    assert run(tmp_path) == 2
    assert not (tmp_path / "configs").exists()
    lines = capsys.readouterr().out.splitlines()
    assert (
        "refused, nothing written: the both_ends_relayer's 2 usable boxes in the MSA do not "
        "all overlap; the layers need a stationary node (lnk_02)"
    ) in lines
    assert not any(text.startswith("dropped") for text in lines)


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
        for point, name in zip(frame.geometry, names_in_order, strict=True):
            assert (point.x, point.y) == pytest.approx(centre_m(name), abs=1e-6)
        assert list(frame["box_half_width_m"]) == pytest.approx(
            [east_west_half_m(name) for name in names_in_order], abs=1e-9
        )
    printed = capsys.readouterr().out
    assert not any(line.startswith("dropped") for line in printed.splitlines())
    assert coordinates_in(printed + repr(events)) == []


def test_command_refuses_an_unexpected_pair_count(tmp_path: Path, capture: Path) -> None:
    assert run(tmp_path, expect_pairs="8") == 2
    assert not (tmp_path / "configs").exists()


@pytest.mark.parametrize("name", ["rx_real.parquet", "engine-inputs.partial"])
def test_command_refuses_to_overwrite(tmp_path: Path, capture: Path, name: str) -> None:
    out = tmp_path / "configs" / "x72y429"
    out.mkdir(parents=True)
    existing = out / name
    existing.write_bytes(b"earlier")
    assert run(tmp_path) == 2
    assert existing.read_bytes() == b"earlier"
    assert [path.name for path in out.iterdir()] == [name]


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


REPORT_PREFIXES = ("dropped", "counts check", "sites by", "sites only", "left out", "counts for")
GATEWAY_LINE = re.compile(r"[0-9a-f]{64} (both_ends|gateway_only)_gateway: ")


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


def assert_report(tmp_path: Path, printed: str) -> None:
    """The report holds every recorded terminal line and no coordinate (Casey, issue #55, 7)."""
    text = report_text(tmp_path)
    assert text.startswith("# engine_inputs --sites-only, x72y429\n")
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
    assert list(sites.columns) == ["site_id", "geometry", "box_half_width_m", "role"]
    assert sites.crs.to_epsg() == 26915
    roles = dict(zip(SITE_ORDER, SITE_ROLES, strict=True))
    assert list(sites["site_id"]) == [pseudonym(name) for name in kept]
    assert list(sites["role"]) == [roles[name] for name in kept]
    for point, name in zip(sites.geometry, kept, strict=True):
        assert (point.x, point.y) == pytest.approx(centre_m(name), abs=1e-6)
    assert list(sites["box_half_width_m"]) == pytest.approx(
        [east_west_half_m(name) for name in kept], abs=1e-9
    )
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
    assert report_lines(tmp_path)[-1] == counts_for_issue(lines)
    assert counts_for_issue(lines) == (
        "counts for issue #44: pairs public 4, MeshSTL 2, shared 1, union 5; pairs touching "
        "the tile 3 (both ends 1); nodes selected 3; extent pass 3, fail 0; pairs dropped 0 "
        "(straddles the tile edge 0, fails the DEM edge check 0); sites written 3, left out 0; "
        "no real receiver written; relayers not DEM-checked"
    )
    assert "both_ends_relayer" not in printed
    assert not any(text.startswith(("dropped", "left out")) for text in lines)
    assert coordinates_in(printed + repr(events)) == []


def test_sites_only_writes_the_gateway_of_a_both_ends_pair_its_relayer_drops(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A straddles, so A to G1 is dropped; G1 is still a site, and the run does not stop."""
    build_dem(tmp_path / "snapshots")
    build_capture(tmp_path / "snapshots", straddler="A")
    with capture_logs() as events:
        assert run_sites_only(tmp_path) == 0
    assert_sites(tmp_path, SITE_ORDER)
    printed = capsys.readouterr().out
    lines = printed.splitlines()
    assert [text for text in lines if text.startswith("dropped")] == [
        names("dropped both-ends pair {A} to {G1}: relayer straddles the tile edge")
    ]
    assert not any(text.startswith("left out") for text in lines)
    assert counts_for_issue(lines).endswith(
        "pairs dropped 1 (straddles the tile edge 1, fails the DEM edge check 0); "
        f"sites written 3, left out 0; {NO_RECEIVER}"
    )
    assert_report(tmp_path, printed)
    assert coordinates_in(printed + repr(events)) == []


@pytest.mark.parametrize(
    ("options", "left_out", "kept", "dropped", "counts"),
    [
        (
            {"straddler": "G2"},
            "left out {G2} gateway_only_gateway: straddles the tile edge",
            ("G1", "G3"),
            ["dropped gateway-only pair {B} to {G2}: gateway straddles the tile edge"],
            "nodes selected 2; extent pass 2, fail 0; pairs dropped 1 (straddles the tile "
            "edge 1, fails the DEM edge check 0); sites written 2, left out 1",
        ),
        (
            {"g3_at_edge": True},
            "left out {G3} gateway_only_gateway: fails the DEM edge check",
            ("G1", "G2"),
            ["dropped gateway-only pair {C} to {G3}: gateway fails the DEM edge check"],
            "nodes selected 3; extent pass 2, fail 1; pairs dropped 1 (straddles the tile "
            "edge 0, fails the DEM edge check 1); sites written 2, left out 1",
        ),
        (
            {"moved_g3": True},
            "left out {G3} gateway_only_gateway: is not stationary under lnk_02",
            ("G1", "G2"),
            [],
            "nodes selected 2; extent pass 2, fail 0; pairs dropped 0 (straddles the tile "
            "edge 0, fails the DEM edge check 0); sites written 2, left out 1",
        ),
    ],
)
def test_sites_only_leaves_out_a_gateway_with_its_reason(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    options: dict[str, Any],
    left_out: str,
    kept: tuple[str, ...],
    dropped: list[str],
    counts: str,
) -> None:
    build_dem(tmp_path / "snapshots")
    build_capture(tmp_path / "snapshots", **options)
    with capture_logs() as events:
        assert run_sites_only(tmp_path) == 0
    assert_sites(tmp_path, kept)
    printed = capsys.readouterr().out
    lines = printed.splitlines()
    assert [text for text in lines if text.startswith("left out")] == [names(left_out)]
    assert [text for text in lines if text.startswith("dropped")] == [names(t) for t in dropped]
    line = counts_for_issue(lines)
    assert line.endswith(f"{counts}; {NO_RECEIVER}")
    assert not any(node.pseudonym in line for node in NODES.values())
    assert_report(tmp_path, printed)
    assert names(left_out) in report_lines(tmp_path)
    assert coordinates_in(printed + repr(events)) == []


D_STRADDLES = ["dropped other pair {D} to {E}: relayer straddles the tile edge"]


@pytest.mark.parametrize(
    ("options", "expect_pairs", "differences", "dropped"),
    [
        ({}, "8", "5 pairs, not the 8 expected", []),
        ({"straddler": "D"}, "5", "pairs touching x72y429 are not 3", D_STRADDLES),
        (
            {"straddler": "D"},
            "8",
            "5 pairs, not the 8 expected; pairs touching x72y429 are not 3",
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


def test_sites_only_roles_follow_the_counted_states(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """B straddles and counts as in the tile, so B to G2 counts as a both-ends pair.

    G2 is then written as a second both_ends_gateway (decided without Casey,
    PR #57). The both-ends pairs come first in the capture's pair order, A to
    G1 before B to G2 because A's pseudonym sorts before B's, then C to G3.
    """
    build_dem(tmp_path / "snapshots")
    build_capture(tmp_path / "snapshots", straddler="B")
    with capture_logs() as events:
        assert run_sites_only(tmp_path) == 0
    out = tmp_path / "configs" / "x72y429"
    assert sorted(path.name for path in out.iterdir()) == [
        "engine_inputs_report.md",
        "tx_sites_real.parquet",
    ]
    sites = gpd.read_parquet(out / "tx_sites_real.parquet")
    assert list(sites["site_id"]) == [pseudonym(name) for name in ("G1", "G2", "G3")]
    assert list(sites["role"]) == [
        "both_ends_gateway",
        "both_ends_gateway",
        "gateway_only_gateway",
    ]
    printed = capsys.readouterr().out
    lines = printed.splitlines()
    assert (
        "counts check against 2026-11-08: differs (2 pairs have both ends in x72y429, not one; "
        "1 pairs have only the gateway end in x72y429, not two); --sites-only reports it and "
        "does not refuse"
    ) in lines
    assert [text for text in lines if text.startswith("dropped")] == [
        names("dropped both-ends pair {B} to {G2}: relayer straddles the tile edge")
    ]
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
    """The strict path is unchanged: a selected gateway that is not stationary refuses."""
    build_dem(tmp_path / "snapshots")
    build_capture(tmp_path / "snapshots", moved_g3=True)
    assert run(tmp_path) == 2
    assert not (tmp_path / "configs").exists()
    assert (
        "refused, nothing written: the gateway_only_gateway's 2 usable boxes in the MSA do not "
        "all overlap; the layers need a stationary node (lnk_02)"
    ) in capsys.readouterr().out.splitlines()
