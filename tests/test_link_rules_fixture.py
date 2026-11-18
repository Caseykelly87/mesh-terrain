"""The link-rules conformance fixture is well formed (issue #55, meeting point 1).

This checks the shape of `tests/fixtures/link_rules/` only: every case names
rules the contract has, every expected row refers to inputs and boxes that
exist and has only the README's keys, every box is named by an expected row,
a single-cell box is one grid cell and the cell of a position in its case, a
merged box's edges are edges of its case's cells, a kept hour names the box
each of its receipts uses, every position at 1 to 31 bits sits on the firmware
grid, and ids are unique. It implements no link rule and never checks an
expected outcome; the SQL models and the ingester's Python are compared
against the fixture in their own units.
"""

import copy
import json
import re
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ingester.allowlist import MQTT_MESSAGE

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "link_rules"
CASES = FIXTURE / "cases"
CONTRACT = REPO_ROOT / "docs" / "data-quality-contract.md"
RULE_ROW = re.compile(r"^\| `([a-z]{3}_\d{2})` \|", re.MULTILINE)
CASE_ID = re.compile(r"^(c\d{2})_[a-z0-9_]+$")

CASE_KEYS = (
    "case_id",
    "title",
    "rules",
    "reasoning",
    "messages",
    "boxes",
    "expected_receipts",
    "expected_link_hours",
)
REQUIRED_MESSAGE_KEYS = frozenset(
    {
        "delivery_key",
        "received_at",
        "origin_pseudonym",
        "origin_low_byte",
        "gateway_pseudonym",
        "gateway_low_byte",
        "via_mqtt",
        "seen_public",
        "seen_meshstl",
        "packet_id",
        "relay_node",
        "portnum",
    }
)
OPTIONAL_MESSAGE_KEYS = frozenset(
    {
        "topic",
        "decode_status",
        "rx_time",
        "rx_snr",
        "rx_rssi",
        "position_source",
        "latitude_i",
        "longitude_i",
        "precision_bits",
        "neighbour_pseudonyms",
        "neighbour_low_bytes",
    }
)
BOX_KEYS = frozenset(
    {
        "south_i",
        "north_i",
        "west_i",
        "east_i",
        "merged",
        "precision_bits",
        "half_width_ns_m",
        "half_width_ew_m",
    }
)
REACHES_LNK_05 = ("relayer_box", "gateway_box", "distance_m")
KEPT_HOUR_KEYS = (
    "median_snr_db",
    "median_rssi_dbm",
    "distance_m",
    "relayer_box",
    "relayer_precise_source",
    "gateway_box",
    "gateway_precise_source",
    "position_uncertainty_m",
)
RECEIPT_KEYS = frozenset(
    {"delivery_key", "outcome", "rule", "relayer", "link_source", *REACHES_LNK_05}
)
HOUR_KEYS = frozenset({"relayer", "gateway", "hour", "receipts", "packets", "outcome", "rule"})


def unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """A JSON object hook that refuses a repeated key instead of keeping the last."""
    counts = Counter(key for key, _ in pairs)
    repeated = sorted(key for key, count in counts.items() if count > 1)
    if repeated:
        raise ValueError(f"repeated keys {repeated}")
    return dict(pairs)


def load_cases() -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
        for path in sorted(CASES.glob("*.json"))
    ]


def contract_rules() -> frozenset[str]:
    return frozenset(RULE_ROW.findall(CONTRACT.read_text(encoding="utf-8")))


def utc(text: str) -> datetime:
    assert text.endswith("Z"), f"{text} is not written in UTC with a Z"
    return datetime.fromisoformat(text).astimezone(UTC)


def prefix(case: dict[str, Any]) -> str:
    match = CASE_ID.match(case["case_id"])
    assert match, f"{case['case_id']} is not c<two digits>_<words>"
    return match.group(1) + "-"


def test_the_fixture_has_a_readme_and_cases() -> None:
    assert (FIXTURE / "README.md").is_file()
    assert load_cases()


def test_case_files_have_the_fixed_keys_and_their_own_names() -> None:
    for path, case in zip(sorted(CASES.glob("*.json")), load_cases(), strict=True):
        assert tuple(case) == CASE_KEYS, path.name
        assert path.stem == case["case_id"]
        assert case["title"] and case["reasoning"], path.name


def test_case_numbers_are_unique() -> None:
    numbers = Counter(prefix(case) for case in load_cases())
    assert [number for number, count in numbers.items() if count > 1] == []


def test_the_readme_lists_every_case() -> None:
    readme = (FIXTURE / "README.md").read_text(encoding="utf-8")
    missing = [case["case_id"] for case in load_cases() if f"`{case['case_id']}`" not in readme]
    assert missing == []


def cited_rules(case: dict[str, Any]) -> set[str]:
    rows = case["expected_receipts"] + case["expected_link_hours"]
    return {row["rule"] for row in rows if "rule" in row}


def test_every_rule_named_is_a_contract_rule_the_case_lists() -> None:
    rules = contract_rules()
    for case in load_cases():
        assert case["rules"], case["case_id"]
        assert set(case["rules"]) <= rules, case["case_id"]
        assert cited_rules(case) <= set(case["rules"]), case["case_id"]


def check_message_types(message: dict[str, Any]) -> None:
    key = message["delivery_key"]
    utc(message["received_at"])
    for flag in ("via_mqtt", "seen_public", "seen_meshstl"):
        assert isinstance(message[flag], bool), key
    for byte in ("origin_low_byte", "gateway_low_byte"):
        assert message[byte] is None or 0 <= message[byte] <= 255, key
    assert message["relay_node"] is None or 1 <= message["relay_node"] <= 255, key
    snr = message.get("rx_snr")
    assert snr is None or snr == "NaN" or isinstance(snr, int | float), key
    rssi = message.get("rx_rssi")
    assert rssi is None or isinstance(rssi, int), key
    names = message.get("neighbour_pseudonyms")
    assert (names is None) == (message.get("neighbour_low_bytes") is None), key
    assert names is None or len(names) == len(message["neighbour_low_bytes"]), key


def test_messages_hold_raw_columns_with_valid_types() -> None:
    raw_columns = {column.name for column in MQTT_MESSAGE}
    assert REQUIRED_MESSAGE_KEYS | OPTIONAL_MESSAGE_KEYS <= raw_columns
    for case in load_cases():
        for message in case["messages"]:
            assert REQUIRED_MESSAGE_KEYS <= set(message), message.get("delivery_key")
            assert set(message) <= REQUIRED_MESSAGE_KEYS | OPTIONAL_MESSAGE_KEYS
            check_message_types(message)


def test_delivery_keys_are_unique_across_the_fixture() -> None:
    keys = Counter(m["delivery_key"] for case in load_cases() for m in case["messages"])
    assert [key for key, count in keys.items() if count > 1] == []


def node_names(case: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for message in case["messages"]:
        names |= {message["origin_pseudonym"], message["gateway_pseudonym"]}
        names |= set(message.get("neighbour_pseudonyms") or [])
    for row in case["expected_receipts"]:
        names |= {row["relayer"]} if row.get("relayer") else set()
    for hour in case["expected_link_hours"]:
        names |= {hour["relayer"], hour["gateway"]}
    return names - {None}


def test_no_two_cases_share_a_node() -> None:
    for case in load_cases():
        strangers = [name for name in node_names(case) if not name.startswith(prefix(case))]
        assert strangers == [], case["case_id"]


def test_copies_of_one_packet_appear_only_in_cases_on_ing_06() -> None:
    for case in load_cases():
        copies = Counter(
            (m["origin_pseudonym"], m["packet_id"], m["gateway_pseudonym"])
            for m in case["messages"]
            if m["packet_id"] is not None
        )
        repeated = [key for key, count in copies.items() if count > 1]
        assert not repeated or "ing_06" in case["rules"], case["case_id"]


def on_firmware_grid(value: int, bits: int) -> bool:
    """For 1 to 31 bits the firmware sends the cell's low edge plus half its width."""
    width = 1 << (32 - bits)
    return (value - width // 2) % width == 0


def test_positions_at_1_to_31_bits_are_on_the_firmware_grid() -> None:
    for case in load_cases():
        for message in case["messages"]:
            bits = message.get("precision_bits")
            latitude, longitude = message.get("latitude_i"), message.get("longitude_i")
            if bits is None or latitude is None or longitude is None or not 1 <= bits <= 31:
                continue
            assert on_firmware_grid(latitude, bits), message["delivery_key"]
            assert on_firmware_grid(longitude, bits), message["delivery_key"]


Edges = tuple[int, int, int, int]


def cell_edges(message: dict[str, Any]) -> Edges | None:
    """South, north, west and east of a stored position's cell, half-open, as `lnk_02` reads it."""
    bits = message.get("precision_bits")
    latitude, longitude = message.get("latitude_i"), message.get("longitude_i")
    if bits is None or latitude is None or longitude is None or not 1 <= bits <= 32:
        return None
    width = 1 << (32 - bits)
    centre_offset = 0 if bits == 32 else width // 2
    south, west = latitude - centre_offset, longitude - centre_offset
    return (south, south + width, west, west + width)


def box_edges(box: dict[str, Any]) -> Edges:
    return (box["south_i"], box["north_i"], box["west_i"], box["east_i"])


def is_one_grid_cell(box: dict[str, Any]) -> bool:
    width = 1 << (32 - box["precision_bits"])
    spans = (box["north_i"] - box["south_i"], box["east_i"] - box["west_i"])
    return spans == (width, width) and box["south_i"] % width == 0 == box["west_i"] % width


def check_box_against_positions(label: str, box: dict[str, Any], cells: list[Edges]) -> None:
    """A single cell is the cell of a position in its case; a merged box's edges are cell edges."""
    if box["merged"]:
        assert not is_one_grid_cell(box), f"{label}: merged is true on one grid cell"
        for side, edge in enumerate(box_edges(box)):
            assert edge in {cell[side] for cell in cells}, f"{label}: an edge no cell has"
        return
    assert is_one_grid_cell(box), f"{label}: not one grid cell at its precision_bits"
    assert box_edges(box) in cells, f"{label}: not the cell of any position in the case"


def referenced_boxes(case: dict[str, Any]) -> set[str]:
    rows = case["expected_receipts"] + case["expected_link_hours"]
    return {row[end] for row in rows for end in ("relayer_box", "gateway_box") if end in row}


def check_boxes(case: dict[str, Any]) -> None:
    cells = [edges for m in case["messages"] if (edges := cell_edges(m)) is not None]
    unused = sorted(set(case["boxes"]) - referenced_boxes(case))
    assert unused == [], f"{case['case_id']}: no expected row names {unused}"
    for name, box in case["boxes"].items():
        label = f"{case['case_id']} {name}"
        assert set(box) == BOX_KEYS, label
        assert box["south_i"] < box["north_i"] and box["west_i"] < box["east_i"], label
        assert isinstance(box["merged"], bool), label
        assert 1 <= box["precision_bits"] <= 32, label
        assert box["half_width_ns_m"] > 0 and box["half_width_ew_m"] > 0, label
        check_box_against_positions(label, box, cells)


def test_boxes_are_well_formed() -> None:
    for case in load_cases():
        check_boxes(case)


def check_receipt(row: dict[str, Any], case: dict[str, Any]) -> None:
    label = f"{case['case_id']} {row['delivery_key']}"
    assert set(row) <= RECEIPT_KEYS, f"{label}: unknown keys {sorted(set(row) - RECEIPT_KEYS)}"
    assert row["outcome"] in {"counted", "dropped"}, label
    assert (row["outcome"] == "dropped") == ("rule" in row), label
    if row["outcome"] == "counted":
        assert row.get("relayer") and row.get("link_source"), label
        assert all(key in row for key in REACHES_LNK_05), label
    assert row.get("link_source") in {None, "relay_node", "neighbor_info"}, label
    assert ("link_source" in row) == ("relayer" in row), label
    assert all(key in row for key in REACHES_LNK_05) or not any(
        key in row for key in REACHES_LNK_05
    ), label
    for box in ("relayer_box", "gateway_box"):
        assert box not in row or row[box] in case["boxes"], label


def check_expected_receipts(case: dict[str, Any]) -> None:
    messages = {m["delivery_key"] for m in case["messages"]}
    listed = Counter(row["delivery_key"] for row in case["expected_receipts"])
    assert set(listed) <= messages, case["case_id"]
    assert [key for key, count in listed.items() if count > 1] == [], case["case_id"]
    with_relay = {m["delivery_key"] for m in case["messages"] if m["relay_node"] is not None}
    assert with_relay <= set(listed), case["case_id"]
    for row in case["expected_receipts"]:
        check_receipt(row, case)


def test_expected_receipts_refer_to_messages_and_boxes_that_exist() -> None:
    for case in load_cases():
        check_expected_receipts(case)


def hour_of(text: str) -> datetime:
    return utc(text).replace(minute=0, second=0, microsecond=0)


def check_hour(hour: dict[str, Any], case: dict[str, Any], counted: dict[str, Any]) -> None:
    """A kept hour names the one box per end that each of its counted receipts uses."""
    label = f"{case['case_id']} {hour['relayer']} {hour['hour']}"
    allowed = HOUR_KEYS | set(KEPT_HOUR_KEYS) if hour["outcome"] == "kept" else HOUR_KEYS
    assert set(hour) <= allowed, f"{label}: unknown keys {sorted(set(hour) - allowed)}"
    assert utc(hour["hour"]) == hour_of(hour["hour"]), label
    assert hour["outcome"] in {"kept", "dropped"}, label
    assert (hour["outcome"] == "dropped") == ("rule" in hour), label
    assert hour["packets"] == len(hour["receipts"]) > 0, label
    if hour["outcome"] != "kept":
        return
    assert all(key in hour for key in KEPT_HOUR_KEYS), label
    for end in ("relayer_box", "gateway_box"):
        assert hour[end] in case["boxes"], label
        used = sorted({counted[key][end] for key in hour["receipts"]})
        assert used == [hour[end]], f"{label}: {end} {hour[end]}, its receipts use {used}"


def check_link_hours(case: dict[str, Any]) -> None:
    messages = {m["delivery_key"]: m for m in case["messages"]}
    counted = {
        row["delivery_key"]: row for row in case["expected_receipts"] if row["outcome"] == "counted"
    }
    placed = Counter(key for hour in case["expected_link_hours"] for key in hour["receipts"])
    assert set(placed) == set(counted), case["case_id"]
    assert max(placed.values(), default=1) == 1, case["case_id"]
    hours = case["expected_link_hours"]
    grains = Counter((hour["relayer"], hour["gateway"], hour["hour"]) for hour in hours)
    assert max(grains.values(), default=1) == 1, case["case_id"]
    for hour in case["expected_link_hours"]:
        check_hour(hour, case, counted)
        for key in hour["receipts"]:
            assert counted[key]["relayer"] == hour["relayer"], key
            assert messages[key]["gateway_pseudonym"] == hour["gateway"], key
            assert hour_of(messages[key]["received_at"]) == utc(hour["hour"]), key


def test_every_counted_receipt_is_in_exactly_one_link_hour_of_its_own() -> None:
    for case in load_cases():
        check_link_hours(case)


def check_case(case: dict[str, Any]) -> None:
    """Every per-case check, so that a malformed edit to one case can be shown to fail."""
    check_boxes(case)
    check_expected_receipts(case)
    check_link_hours(case)


def case_by_number(number: str) -> dict[str, Any]:
    (case,) = [case for case in load_cases() if case["case_id"].startswith(number + "_")]
    return case


def wrong_width(case: dict[str, Any]) -> None:
    case["boxes"]["r20"]["north_i"] += 4096


def merged_single_cell(case: dict[str, Any]) -> None:
    case["boxes"]["r20"]["merged"] = True


def hour_names_a_box_its_receipts_do_not_use(case: dict[str, Any]) -> None:
    case["expected_link_hours"][0]["relayer_box"] = "g16"


def unknown_key_in_a_receipt(case: dict[str, Any]) -> None:
    case["expected_receipts"][0]["note"] = "x"


def unknown_key_in_an_hour(case: dict[str, Any]) -> None:
    case["expected_link_hours"][0]["note"] = "x"


def kept_hour_key_in_a_dropped_hour(case: dict[str, Any]) -> None:
    case["expected_link_hours"][0]["median_snr_db"] = 6.0


def box_nothing_refers_to(case: dict[str, Any]) -> None:
    case["boxes"]["spare"] = dict(case["boxes"]["r20"])


@pytest.mark.parametrize(
    ("number", "edit", "message"),
    [
        ("c01", wrong_width, "not one grid cell"),
        ("c01", merged_single_cell, "merged is true on one grid cell"),
        ("c01", hour_names_a_box_its_receipts_do_not_use, "its receipts use"),
        ("c01", unknown_key_in_a_receipt, "unknown keys"),
        ("c01", unknown_key_in_an_hour, "unknown keys"),
        ("c24", kept_hour_key_in_a_dropped_hour, "unknown keys"),
        ("c01", box_nothing_refers_to, "no expected row names"),
    ],
)
def test_a_malformed_edit_fails_the_checks(
    number: str, edit: Callable[[dict[str, Any]], None], message: str
) -> None:
    """The reviewer's edits on PR #66, each of which passed the checks at 75a5b8a."""
    case = copy.deepcopy(case_by_number(number))
    check_case(case)
    edit(case)
    with pytest.raises(AssertionError, match=message):
        check_case(case)


@pytest.mark.parametrize(
    ("value", "bits", "expected"),
    [
        (-6, 30, True),
        (-5, 30, False),
        (385_974_272, 16, True),
        (385_974_272 + 32_768, 16, False),
        (-903_970_816, 16, True),
        (-903_970_816 + 1, 16, False),
    ],
)
def test_the_grid_check_reads_negative_values_as_the_firmware_does(
    value: int, bits: int, expected: bool
) -> None:
    """At 30 bits the width is 4: -5 lies in [-8, -4), whose stored centre is -6.

    At 16 bits the width is 65536: 385 941 504 is a multiple of it, so its
    centre is 385 974 272; -904 003 584 is one too, so -903 970 816 is a centre.
    """
    assert on_firmware_grid(value, bits) is expected
