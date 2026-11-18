"""The ingester's link-hour rules against the conformance fixture (issue #55, meeting point 1).

Every case under `tests/fixtures/link_rules/cases/` is read from its JSON, with
no database, turned into rows shaped like `raw.mqtt_message` and run through
`ingester.link_rules.apply_link_rules` with the MSA polygon from the dbt seed.
The comparison follows the fixture README: the counted set and each dropped
receipt's rule exactly; relayer and `link_source` exactly; boxes by all four
edges, `merged` and `precision_bits` exactly; distances within 1e-4 m;
half-widths and `position_uncertainty_m` within 1e-6 m; medians and the
precise flags exactly. Each case runs alone, and all cases run together as one
table, as the SQL side loads them, where the one-box drops count 2 and the
direct hops NeighborInfo contradicts count 3.
"""

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from ingester.link_cells import LinkBox
from ingester.link_rules import LinkHour, LinkRulesResult, ReceiptOutcome, apply_link_rules
from ingester.msa import msa_contains

CASES = Path(__file__).resolve().parent / "fixtures" / "link_rules" / "cases"
CASE_FILES = sorted(CASES.glob("c*.json"))
DISTANCE_TOLERANCE_M = 1e-4
HALF_WIDTH_TOLERANCE_M = 1e-6
ONE_BOX_DROPS_OVER_THE_FIXTURE = 2
# lnk_01_direct_hop_contradicted across the fixture (README, Casey's Q4 (a); c48).
DIRECT_HOPS_CONTRADICTED_OVER_THE_FIXTURE = 3


def load_case(path: Path) -> dict[str, Any]:
    case: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return case


def to_row(message: dict[str, Any]) -> dict[str, Any]:
    """A raw-shaped row: an absent column is null, "NaN" is the IEEE value, times are UTC."""
    row = dict(message)
    row["received_at"] = datetime.fromisoformat(message["received_at"])
    row.setdefault("topic", f"msh/US/2/e/LongFast/{message['gateway_pseudonym']}")
    row.setdefault("decode_status", "ok")
    for column in ("rx_snr", "rx_rssi", "position_source", "latitude_i", "longitude_i"):
        row.setdefault(column, None)
    for column in ("precision_bits", "neighbour_pseudonyms", "neighbour_low_bytes"):
        row.setdefault(column, None)
    if row["rx_snr"] == "NaN":
        row["rx_snr"] = math.nan
    return row


@pytest.fixture(scope="module")
def inside_msa() -> Any:
    return msa_contains()


def assert_box(found: LinkBox | None, expected: dict[str, Any], where: str) -> None:
    assert found is not None, f"{where}: no box"
    edges = (found.south_i, found.north_i, found.west_i, found.east_i)
    wanted = (expected["south_i"], expected["north_i"], expected["west_i"], expected["east_i"])
    assert edges == wanted, f"{where}: edges"
    assert found.merged == expected["merged"], f"{where}: merged"
    assert found.precision_bits == expected["precision_bits"], f"{where}: precision_bits"
    assert found.half_width_ns_m == pytest.approx(
        expected["half_width_ns_m"], abs=HALF_WIDTH_TOLERANCE_M
    ), f"{where}: half_width_ns_m"
    assert found.half_width_ew_m == pytest.approx(
        expected["half_width_ew_m"], abs=HALF_WIDTH_TOLERANCE_M
    ), f"{where}: half_width_ew_m"


def assert_receipt(
    found: ReceiptOutcome, expected: dict[str, Any], boxes: dict[str, Any], where: str
) -> None:
    assert found.outcome == expected["outcome"], f"{where}: outcome"
    assert found.rule == expected.get("rule"), f"{where}: rule"
    assert found.relayer == expected.get("relayer"), f"{where}: relayer"
    assert found.link_source == expected.get("link_source"), f"{where}: link_source"
    if "relayer_box" not in expected:
        assert found.relayer_box is None and found.gateway_box is None, f"{where}: boxes"
        assert found.distance_m is None, f"{where}: distance"
        return
    assert_box(found.relayer_box, boxes[expected["relayer_box"]], f"{where} relayer_box")
    assert_box(found.gateway_box, boxes[expected["gateway_box"]], f"{where} gateway_box")
    assert found.distance_m == pytest.approx(expected["distance_m"], abs=DISTANCE_TOLERANCE_M), (
        f"{where}: distance_m"
    )


def assert_link_hour(found: LinkHour, expected: dict[str, Any], boxes: dict[str, Any]) -> None:
    where = f"{expected['relayer']} to {expected['gateway']} {expected['hour']}"
    assert sorted(found.receipts) == sorted(expected["receipts"]), f"{where}: receipts"
    assert found.packets == expected["packets"], f"{where}: packets"
    assert found.outcome == expected["outcome"], f"{where}: outcome"
    assert found.rule == expected.get("rule"), f"{where}: rule"
    if expected["outcome"] != "kept":
        return
    assert found.median_snr_db == expected["median_snr_db"], f"{where}: median_snr_db"
    assert found.median_rssi_dbm == expected["median_rssi_dbm"], f"{where}: median_rssi_dbm"
    assert found.distance_m == pytest.approx(expected["distance_m"], abs=DISTANCE_TOLERANCE_M), (
        f"{where}: distance_m"
    )
    assert_box(found.relayer_box, boxes[expected["relayer_box"]], f"{where} relayer_box")
    assert_box(found.gateway_box, boxes[expected["gateway_box"]], f"{where} gateway_box")
    assert found.relayer_precise_source is expected["relayer_precise_source"], f"{where}: flag"
    assert found.gateway_precise_source is expected["gateway_precise_source"], f"{where}: flag"
    assert found.position_uncertainty_m == pytest.approx(
        expected["position_uncertainty_m"], abs=HALF_WIDTH_TOLERANCE_M
    ), f"{where}: position_uncertainty_m"


def assert_case(case: dict[str, Any], result: LinkRulesResult) -> None:
    """Compare one case's expected rows with a result that holds at least that case's rows."""
    prefix = case["case_id"][:3] + "-"
    counted = {
        key
        for key, outcome in result.receipts.items()
        if key.startswith(prefix) and outcome.outcome == "counted"
    }
    expected_counted = {
        row["delivery_key"] for row in case["expected_receipts"] if row["outcome"] == "counted"
    }
    assert counted == expected_counted, "counted set"
    for expected in case["expected_receipts"]:
        key = expected["delivery_key"]
        assert_receipt(result.receipts[key], expected, case["boxes"], key)
    hours = {
        (hour.relayer, hour.gateway, hour.hour.strftime("%Y-%m-%dT%H:%M:%SZ")): hour
        for hour in result.link_hours
        if hour.relayer.startswith(prefix)
    }
    expected_hours = {
        (h["relayer"], h["gateway"], h["hour"]): h for h in case["expected_link_hours"]
    }
    assert set(hours) == set(expected_hours), "link-hours"
    for key, expected_hour in expected_hours.items():
        assert_link_hour(hours[key], expected_hour, case["boxes"])


@pytest.mark.parametrize("path", CASE_FILES, ids=[path.stem[:3] for path in CASE_FILES])
def test_each_case_gives_its_expected_rows(path: Path, inside_msa: Any) -> None:
    case = load_case(path)
    result = apply_link_rules([to_row(m) for m in case["messages"]], inside_msa)
    assert_case(case, result)


def test_every_case_loaded_together_gives_the_same_rows(inside_msa: Any) -> None:
    cases = [load_case(path) for path in CASE_FILES]
    rows = [to_row(message) for case in cases for message in case["messages"]]
    result = apply_link_rules(rows, inside_msa)
    for case in cases:
        assert_case(case, result)
    assert result.one_box_dropped_hours == ONE_BOX_DROPS_OVER_THE_FIXTURE
    assert result.direct_hop_contradicted == DIRECT_HOPS_CONTRADICTED_OVER_THE_FIXTURE


def test_the_fixture_has_cases() -> None:
    assert len(CASE_FILES) >= 51
