"""Dagster asset that loads one snapshot into raw, with its contract checks (ADR 0005).

Started by hand with the snapshot id as run config; there is no schedule and
no sensor. The asset loads in one transaction, then reports five checks:
`ing_07_second_load` (a second load inserts nothing), `ing_03_reconcile` (raw
rows and counts against the manifest per hour and broker, ingester.load.reconcile),
`snp_01_content_hash` (each raw table's data content hash against the manifest),
`pos_03_not_both_zero` (warning severity: how many rows sit at 0, 0, which raw
keeps as the source sent them; Casey, 2026-11-14) and `ing_04_decode_rate`
(warning severity below 90 percent, since the MeshSTL broker's own channel is
decrypt_failed by design). No check here is blocking, so a failed check of either
severity is reported on the asset and does not fail the run.
"""

from typing import Any

import psycopg
from dagster import (
    AssetCheckResult,
    AssetCheckSeverity,
    AssetCheckSpec,
    Config,
    MaterializeResult,
    asset,
)

from ingester import load
from ingester.settings import get_ingester_settings
from pipeline.common.settings import get_settings

DECODE_RATE_WARN = 0.90
CHECKS = [
    AssetCheckSpec("ing_07_second_load", asset=["raw", "mqtt_snapshot"]),
    AssetCheckSpec("ing_03_reconcile", asset=["raw", "mqtt_snapshot"]),
    AssetCheckSpec("snp_01_content_hash", asset=["raw", "mqtt_snapshot"]),
    AssetCheckSpec("pos_03_not_both_zero", asset=["raw", "mqtt_snapshot"]),
    AssetCheckSpec("ing_04_decode_rate", asset=["raw", "mqtt_snapshot"]),
]


class SnapshotConfig(Config):
    """Which finished snapshot folder under SNAPSHOTS_DIR to load."""

    snapshot_id: str


def _connect() -> psycopg.Connection:
    settings = get_settings()
    return psycopg.connect(
        host=settings.warehouse_host,
        port=settings.warehouse_port,
        user=settings.warehouse_user,
        password=settings.warehouse_password.get_secret_value(),
        dbname=settings.warehouse_db,
    )


# Dagster reads the return annotation to build the asset's outputs and rejects
# MaterializeResult[None], so the bare class stays and mypy is told why.
@asset(key=["raw", "mqtt_snapshot"], check_specs=CHECKS, group_name="raw")
def raw_mqtt_snapshot(config: SnapshotConfig) -> MaterializeResult:  # type: ignore[type-arg]
    """Load one snapshot's delivery and count tables into raw, insert-only."""
    folder = load.snapshot_folder(get_ingester_settings().snapshots_dir, config.snapshot_id)
    with _connect() as conn:
        with conn.transaction():
            inserted = load.load_snapshot(conn, folder)
        with conn.transaction():
            again = load.load_snapshot(conn, folder)
        found: dict[str, Any] = {
            "again": again,
            "problems": load.reconcile(conn, folder),
            "hash_problems": load.hash_problems(conn, folder),
            "zero_positions": load.zero_positions(conn, config.snapshot_id),
            "rates": load.decode_rates(conn, config.snapshot_id),
        }
        counts = load.row_counts(conn, config.snapshot_id)
    return MaterializeResult(
        metadata={"inserted": inserted, "rows": counts, "decode_rates": found["rates"]},
        check_results=check_results(found),
    )


def check_results(found: dict[str, Any]) -> list[AssetCheckResult]:
    """The asset's contract checks from what the load and its queries found."""
    problems, rates = found["problems"], found["rates"]
    low = {flag: rate for flag, rate in rates.items() if rate < DECODE_RATE_WARN}
    return [
        AssetCheckResult(
            check_name="ing_07_second_load",
            passed=not any(found["again"].values()),
            metadata={"inserted_on_second_load": found["again"]},
        ),
        AssetCheckResult(
            check_name="ing_03_reconcile",
            passed=not problems,
            metadata={"problems": problems[:20], "problem_count": len(problems)},
        ),
        AssetCheckResult(
            check_name="snp_01_content_hash",
            passed=not found["hash_problems"],
            metadata={"problems": found["hash_problems"]},
        ),
        AssetCheckResult(
            check_name="pos_03_not_both_zero",
            passed=not found["zero_positions"],
            severity=AssetCheckSeverity.WARN,
            description=f"{sum(found['zero_positions'].values())} rows at 0, 0 (raw keeps them)",
            metadata={"rows_at_zero_zero": found["zero_positions"]},
        ),
        AssetCheckResult(
            check_name="ing_04_decode_rate",
            passed=not low,
            severity=AssetCheckSeverity.WARN,
            metadata={"decode_rates": rates},
        ),
    ]
