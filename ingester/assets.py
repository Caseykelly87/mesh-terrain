"""Dagster asset that loads one snapshot into raw, with its contract checks (ADR 0005).

Started by hand with the snapshot id as run config; there is no schedule and
no sensor. The asset loads in one transaction, then reports three checks:
`ing_07_second_load` (a second load inserts nothing), `ing_03_reconcile` (raw
rows and counts against the manifest per hour, and the manifest's balance per
hour and broker) and `ing_04_decode_rate` (warn below 90 percent, never
blocking, since the MeshSTL broker's own channel is decrypt_failed by design).
"""

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


@asset(key=["raw", "mqtt_snapshot"], check_specs=CHECKS, group_name="raw")
def raw_mqtt_snapshot(config: SnapshotConfig) -> MaterializeResult[None]:
    """Load one snapshot's delivery and count tables into raw, insert-only."""
    folder = load.snapshot_folder(get_ingester_settings().snapshots_dir, config.snapshot_id)
    with _connect() as conn:
        with conn.transaction():
            inserted = load.load_snapshot(conn, folder)
        with conn.transaction():
            again = load.load_snapshot(conn, folder)
        problems = load.reconcile(conn, folder)
        rates = load.decode_rates(conn, config.snapshot_id)
        counts = load.row_counts(conn, config.snapshot_id)
    low = {flag: rate for flag, rate in rates.items() if rate < DECODE_RATE_WARN}
    return MaterializeResult(
        metadata={"inserted": inserted, "rows": counts, "decode_rates": rates},
        check_results=[
            AssetCheckResult(
                check_name="ing_07_second_load",
                passed=not any(again.values()),
                metadata={"inserted_on_second_load": again},
            ),
            AssetCheckResult(
                check_name="ing_03_reconcile",
                passed=not problems,
                metadata={"problems": problems[:20], "problem_count": len(problems)},
            ),
            AssetCheckResult(
                check_name="ing_04_decode_rate",
                passed=not low,
                severity=AssetCheckSeverity.WARN,
                metadata={"decode_rates": rates},
            ),
        ],
    )
