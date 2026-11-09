"""The raw snapshot asset is in the code location with its contract checks and nothing scheduled.

Its body is a thin wrapper over ingester.load, which tests/test_load.py covers
against the warehouse; materialising it here would write a fixture snapshot
into the developer's warehouse, so this test only checks the wiring.
"""

from dagster import AssetCheckKey, AssetKey


def test_the_raw_snapshot_asset_and_its_checks_are_defined() -> None:
    from pipeline.definitions import defs

    graph = defs.resolve_asset_graph()
    key = AssetKey(["raw", "mqtt_snapshot"])
    assert key in graph.get_all_asset_keys()
    checks = {AssetCheckKey(key, name) for name in ("ing_07_second_load", "ing_03_reconcile")}
    checks.add(AssetCheckKey(key, "ing_04_decode_rate"))
    assert checks <= set(graph.asset_check_keys)


def test_nothing_is_scheduled_or_sensed() -> None:
    from pipeline.definitions import defs

    assert not list(defs.schedules or [])
    assert not list(defs.sensors or [])
