"""The link-rules fixture's expected receipts and link-hours, as dbt seeds.

The dbt tests compare `core.fct_rx_observation`, `core.int_link_window` and the
link models' per-receipt and per-hour decisions with two seeds this module
writes: `dbt/seeds/fixtures/expected_link_receipts.csv` and
`dbt/seeds/fixtures/expected_link_hours.csv`.

Nothing here computes an expected value. Every value is copied from the
hand-worked `expected_receipts` and `expected_link_hours` of the cases under
`tests/fixtures/link_rules/cases/`; a named box is written out as its edges,
`merged`, `precision_bits` and half-widths from the case's `boxes`. A key the
README does not list, or a box name the case does not define, stops the seed.

After a fixture change, regenerate the seeds with
`uv run python tests/link_rules_expected.py`.
"""

import csv
import io
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixture_rows import LINK_RULES_CASES, REPO_ROOT, read_cases  # noqa: E402

SEED_FOLDER = REPO_ROOT / "dbt" / "seeds" / "fixtures"
RECEIPTS_SEED = SEED_FOLDER / "expected_link_receipts.csv"
HOURS_SEED = SEED_FOLDER / "expected_link_hours.csv"

BOX_FIELDS = (
    "south_i",
    "north_i",
    "west_i",
    "east_i",
    "merged",
    "precision_bits",
    "half_width_ns_m",
    "half_width_ew_m",
)
END_BOX_COLUMNS = tuple(f"{end}_{field}" for end in ("relayer", "gateway") for field in BOX_FIELDS)

RECEIPT_KEYS = {
    "delivery_key",
    "outcome",
    "rule",
    "relayer",
    "link_source",
    "relayer_box",
    "gateway_box",
    "distance_m",
}
HOUR_KEYS = {
    "relayer",
    "gateway",
    "hour",
    "receipts",
    "packets",
    "outcome",
    "rule",
    "median_snr_db",
    "median_rssi_dbm",
    "distance_m",
    "relayer_box",
    "relayer_precise_source",
    "gateway_box",
    "gateway_precise_source",
    "position_uncertainty_m",
}

RECEIPT_COLUMNS = (
    "case_id",
    "delivery_key",
    "outcome",
    "rule",
    "relayer",
    "link_source",
    *END_BOX_COLUMNS,
    "distance_m",
)
HOUR_COLUMNS = (
    "case_id",
    "relayer",
    "gateway",
    "hour",
    "receipts",
    "packets",
    "outcome",
    "rule",
    "median_snr_db",
    "median_rssi_dbm",
    "distance_m",
    *END_BOX_COLUMNS,
    "relayer_precise_source",
    "gateway_precise_source",
    "position_uncertainty_m",
)


def box_columns(case: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """The relayer and gateway boxes a row names, written out; empty where it names none.

    Raises:
        ValueError: when the row names a box its case does not define.
    """
    columns: dict[str, Any] = dict.fromkeys(END_BOX_COLUMNS)
    for end in ("relayer", "gateway"):
        name = row.get(f"{end}_box")
        if name is None:
            continue
        if name not in case["boxes"]:
            raise ValueError(f"{case['case_id']}: box {name!r} is not in the case's boxes")
        for field in BOX_FIELDS:
            columns[f"{end}_{field}"] = case["boxes"][name][field]
    return columns


def check_keys(case_id: str, row: dict[str, Any], allowed: set[str]) -> None:
    extra = set(row) - allowed
    if extra:
        raise ValueError(f"{case_id}: keys outside the README's lists: {sorted(extra)}")


def receipt_rows(cases: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for case in cases:
        for receipt in case["expected_receipts"]:
            check_keys(case["case_id"], receipt, RECEIPT_KEYS)
            rows.append(
                {
                    "case_id": case["case_id"],
                    **{key: receipt.get(key) for key in RECEIPT_COLUMNS if key in RECEIPT_KEYS},
                    **box_columns(case, receipt),
                }
            )
    return sorted(rows, key=lambda row: row["delivery_key"])


def hour_rows(cases: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for case in cases:
        for hour in case["expected_link_hours"]:
            check_keys(case["case_id"], hour, HOUR_KEYS)
            row = {key: hour.get(key) for key in HOUR_COLUMNS if key in HOUR_KEYS}
            row["receipts"] = " ".join(sorted(hour["receipts"]))
            rows.append({"case_id": case["case_id"], **row, **box_columns(case, hour)})
    return sorted(rows, key=lambda row: (row["relayer"], row["gateway"], row["hour"]))


def cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def seed_text(columns: tuple[str, ...], rows: Iterable[dict[str, Any]]) -> str:
    """A seed CSV: a header, then one line per row, empty for null."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([cell_text(row[column]) for column in columns])
    return buffer.getvalue()


def seeds() -> dict[Path, str]:
    """Each seed file and the text the cases give it."""
    cases = read_cases(LINK_RULES_CASES)
    return {
        RECEIPTS_SEED: seed_text(RECEIPT_COLUMNS, receipt_rows(cases)),
        HOURS_SEED: seed_text(HOUR_COLUMNS, hour_rows(cases)),
    }


if __name__ == "__main__":
    for path, text in seeds().items():
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(REPO_ROOT).as_posix()}")
