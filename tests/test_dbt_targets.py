"""Which schemas each dbt target reads and writes (issue #55, B1).

The development target `dev_wh` is the default and the one `make check` and
`make dbt-build` use: every relation it builds sits in a schema suffixed
`_dev_wh`, and every source reads `raw_dev_wh`, which holds synthetic fixture
tables only. The real `raw`, `staging`, `core` and `analytics` schemas belong
to the target `prod`, which only the lead runs (`make dbt-build-real`).
"""

from typing import Any

import yaml
from dbt_listing import DBT_DIR, DEVELOPMENT_TARGET, REAL_TARGET, dbt_nodes

SUFFIX = f"_{DEVELOPMENT_TARGET}"
REAL_SCHEMAS = {"raw", "staging", "core", "analytics"}
BUILT_TYPES = {"model", "seed", "snapshot", "test"}


def built(nodes: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    """The nodes that create or read a relation in the target's own schemas."""
    return [node for node in nodes if node["resource_type"] in BUILT_TYPES]


def sources(nodes: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    return [node for node in nodes if node["resource_type"] == "source"]


def test_the_default_target_is_the_development_target() -> None:
    profiles = yaml.safe_load((DBT_DIR / "profiles.yml").read_text(encoding="utf-8"))
    profile = profiles["mesh_terrain"]
    assert profile["target"] == DEVELOPMENT_TARGET
    assert set(profile["outputs"]) == {DEVELOPMENT_TARGET, REAL_TARGET}


def test_every_relation_on_the_development_target_is_in_a_suffixed_schema() -> None:
    nodes = built(dbt_nodes(DEVELOPMENT_TARGET))
    assert {node["resource_type"] for node in nodes} >= {"model", "seed", "test"}
    outside = [
        (node["unique_id"], node["schema"]) for node in nodes if not node["schema"].endswith(SUFFIX)
    ]
    assert outside == []


def test_every_source_on_the_development_target_reads_the_synthetic_raw_schema() -> None:
    found = sources(dbt_nodes(DEVELOPMENT_TARGET))
    assert found, "no dbt source is declared, so the source check would pass on nothing"
    assert {node["schema"] for node in found} == {f"raw{SUFFIX}"}


def test_the_real_target_builds_the_real_schemas_and_reads_the_real_raw() -> None:
    nodes = dbt_nodes(REAL_TARGET)
    models = [node for node in built(nodes) if node["resource_type"] in {"model", "seed"}]
    assert {node["schema"] for node in models} <= REAL_SCHEMAS - {"raw"}
    assert {node["schema"] for node in sources(nodes)} == {"raw"}
