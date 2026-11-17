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
    build_capture,
    build_dem,
    centre_m,
)
from structlog.testing import capture_logs

from engine import positions_marker
from engine.points import read_points
from ingester import engine_inputs
from ingester.link_pairs import CaptureLinks, PairActivity
from ingester.tile_selection import (
    FAILS_DEM,
    OTHER_PAIR,
    STRADDLES,
    DroppedPair,
    PlacedNode,
    Selection,
)

# The import above activates the autouse fixture for this module.
_ = no_logger_caching


def pseudonym(name: str) -> str:
    return NODES[name].pseudonym


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
    """Any fixture node's box centre, in metres or 1e-7 degrees, or a St. Louis degree value."""
    numbers = [f"{value:.0f}" for name in NODES for value in centre_m(name)]
    numbers += [str(part) for node in NODES.values() for part in node.box()[:2]]
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
    assert list(sites["site_id"]) == [pseudonym("G1"), pseudonym("G2"), pseudonym("G3")]
    assert list(sites["role"]) == ["both_ends_gateway", "busy_pair_gateway", "busy_pair_gateway"]
    assert list(receivers["rx_id"]) == [pseudonym("A")]
    for frame, column in ((sites, "site_id"), (receivers, "rx_id")):
        for row_id, point in zip(frame[column], frame.geometry, strict=True):
            name = next(key for key, node in NODES.items() if node.pseudonym == row_id)
            assert (point.x, point.y) == pytest.approx(centre_m(name), abs=1e-6)
    assert str(sites["box_half_width_m"].dtype) == "float64"
    assert list(sites["box_half_width_m"]) == pytest.approx(
        [east_west_half_m("G1"), east_west_half_m("G2"), east_west_half_m("G3")], abs=1e-9
    )
    assert list(receivers["box_half_width_m"]) == pytest.approx([east_west_half_m("A")], abs=1e-9)
    assert len(read_points(out / "tx_sites_real.parquet", "site_id")) == 3
    assert len(read_points(out / "rx_real.parquet", "rx_id")) == 1
    printed = capsys.readouterr().out
    assert re.search(rf"{pseudonym('G3')} busy_pair_gateway: .*extent pass", printed)
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


def test_the_command_states_the_busiest_pair_rule(
    tmp_path: Path, capture: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path) == 0
    assert (
        "busiest two pairs: by link-hours over both runs, ties broken by receipts in those "
        "link-hours (Casey, issue #44, engine_inputs Q2 (a), 2026-11-16)"
    ) in capsys.readouterr().out.splitlines()


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


@pytest.mark.parametrize(
    ("options", "line"),
    [
        (
            {"straddler": "G2"},
            "dropped busiest pair {B} to {G2}: gateway straddles the tile edge",
        ),
        (
            {"straddler": "A"},
            "dropped both-ends pair {A} to {G1}: relayer straddles the tile edge",
        ),
        (
            {"g3_at_edge": True},
            "dropped busiest pair {C} to {G3}: gateway fails the DEM edge check",
        ),
    ],
)
def test_a_dropped_selected_pair_stops_the_run_before_any_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], options: dict[str, Any], line: str
) -> None:
    build_dem(tmp_path / "snapshots")
    build_capture(tmp_path / "snapshots", **options)
    with capture_logs() as events:
        assert run(tmp_path) == 2
    assert not (tmp_path / "configs").exists()
    printed = capsys.readouterr().out
    lines = printed.splitlines()
    assert line.format(**{name: pseudonym(name) for name in NODES}) in lines
    assert [text for text in lines if text.startswith("dropped")] == [
        line.format(**{name: pseudonym(name) for name in NODES})
    ]
    assert "refused, nothing written: selected pairs dropped" in printed
    assert not any(text.startswith("counts for") for text in lines)
    assert coordinates_in(printed + repr(events)) == []


def test_a_straddling_node_in_an_unselected_pair_fails_the_touching_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Counted as in the tile, the node makes a fourth touching pair, so the count check refuses."""
    build_dem(tmp_path / "snapshots")
    build_capture(tmp_path / "snapshots", straddler="D")
    assert run(tmp_path) == 2
    assert not (tmp_path / "configs").exists()
    lines = capsys.readouterr().out.splitlines()
    assert (
        "tile x72y429: pairs with both ends 1, only the gateway end 2, only the relayer end 1, "
        "touched 4"
    ) in lines
    assert "refused, nothing written: pairs touching x72y429 are not 3" in lines
    assert not any(text.startswith("dropped") for text in lines)


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
    assert "refused, nothing written: the both_ends_relayer sent 2 boxes" in text
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
        "refused, nothing written: the both_ends_relayer sent 2 boxes during the capture; "
        "the layers need its one stationary box"
    ) in lines
    assert not any(text.startswith("dropped") for text in lines)


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
