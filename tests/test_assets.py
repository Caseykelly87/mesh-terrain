"""The raw snapshot asset is in the code location with its contract checks and nothing scheduled.

Its body is a thin wrapper over ingester.load, which tests/test_load.py covers
against the warehouse; materialising it here would write a fixture snapshot
into the developer's warehouse, so this test only checks the wiring.
"""

from dagster import AssetCheckKey, AssetCheckSeverity, AssetKey

from ingester.assets import check_results


def test_the_raw_snapshot_asset_and_its_checks_are_defined() -> None:
    from pipeline.definitions import defs

    graph = defs.resolve_asset_graph()
    key = AssetKey(["raw", "mqtt_snapshot"])
    assert key in graph.get_all_asset_keys()
    checks = {AssetCheckKey(key, name) for name in ("ing_07_second_load", "ing_03_reconcile")}
    checks.add(AssetCheckKey(key, "ing_04_decode_rate"))
    checks.add(AssetCheckKey(key, "snp_01_content_hash"))
    checks.add(AssetCheckKey(key, "pos_03_not_both_zero"))
    assert checks <= set(graph.asset_check_keys)


def test_nothing_is_scheduled_or_sensed() -> None:
    from pipeline.definitions import defs

    assert not list(defs.schedules or [])
    assert not list(defs.sensors or [])


def test_rows_at_zero_zero_warn_on_the_pos_03_check() -> None:
    found = {
        "again": {"mqtt_message": 0},
        "problems": [],
        "hash_problems": [],
        "zero_positions": {"seen_public, position": 119},
        "rates": {"seen_public": 0.95, "seen_meshstl": 0.95},
    }
    results = {result.check_name: result for result in check_results(found)}
    # Casey, 2026-11-14: an accepted condition warns and reports the count; it is no error.
    assert results["pos_03_not_both_zero"].passed is False
    assert results["pos_03_not_both_zero"].severity == AssetCheckSeverity.WARN
    assert results["pos_03_not_both_zero"].metadata["rows_at_zero_zero"].value == {
        "seen_public, position": 119
    }
    assert results["ing_03_reconcile"].passed is True
