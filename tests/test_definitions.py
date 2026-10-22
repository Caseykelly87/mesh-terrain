"""The one Phase 0 test: the code location loads.

A broken manifest, a bad profile or a misconfigured resource fails here instead of
at webserver start, where the error is buried in a gRPC log.
"""

from dagster import Definitions


def test_definitions_load() -> None:
    from pipeline.definitions import defs

    assert isinstance(defs, Definitions)
    assert "dbt" in defs.resources
    assert defs.resolve_asset_graph() is not None
