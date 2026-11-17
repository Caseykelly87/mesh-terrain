"""Every rule in docs/data-quality-contract.md has a test named for it.

The contract (its opening lines and ADR 0007) accepts a dbt test or a Dagster
asset check, so a test names the rule `lnk_03` in one of three ways:

- a dbt singular test file `assert_lnk_03.sql` or `assert_lnk_03_<what>.sql`;
- a dbt generic test in model YAML with `config: {meta: {contract: lnk_03}}`, or a
  list of ids when one test enforces several rules;
- a Dagster asset check named `lnk_03` or `lnk_03_<what>`, as the existing
  `ing_04_decode_rate` is.

A pytest function does not count, whatever its name: the contract does not
accept one. Rules that no test names yet are on PENDING. The check fails when
a rule off the list has no test, and when a rule on the list has one, so a
rule leaves the list in the same change that adds its test. PENDING may never
hold a rule that was not pending when the list was made (PENDING_AT_B1), so it
only shrinks. The check needs one named test per rule, not every item of the
contract's "Enforced by" column; the reviewer checks that.
"""

import ast
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from dbt_listing import DEVELOPMENT_TARGET, REPO_ROOT, dbt_nodes

CONTRACT = REPO_ROOT / "docs" / "data-quality-contract.md"
RULE_ROW = re.compile(r"^\| `([a-z]{3}_\d{2})` \|", re.MULTILINE)
LEADING_RULE = re.compile(r"^([a-z]{3}_\d{2})(?:_|$)")
SINGULAR_RULE = re.compile(r"^assert_([a-z]{3}_\d{2})(?:_|$)")
ASSET_CHECK_PACKAGES = ("ingester", "pipeline", "engine", "terrain")
ASSET_CHECK_CALLS = {"AssetCheckSpec", "asset_check"}

# The rules pending when this test was written (issue #55, B1, 2026-11-17).
# PENDING must stay a subset of it; do not edit this set.
PENDING_AT_B1 = frozenset(
    {
        *("ing_01", "ing_02", "ing_05", "ing_06"),
        *("pos_01", "pos_02", "pos_04"),
        *("lnk_01", "lnk_02", "lnk_03", "lnk_04", "lnk_05", "lnk_06", "lnk_07", "lnk_08"),
        *("prv_01", "prv_02", "prv_03"),
        *("ter_01", "ter_02", "ter_03", "ter_04", "ter_05", "ter_06", "ter_07"),
        *("val_01", "val_02", "val_03"),
    }
)

# Rules with no dbt test or asset check named for them yet. Remove a rule in
# the change that adds its test.
PENDING = frozenset(
    {
        # ing_01 (key derivation), ing_05 (loader refusal) and prv_01
        # (allowlist) have pytest tests today, which the contract does not
        # count; each needs a dbt test or an asset check named for it.
        "ing_01",
        "ing_02",
        "ing_05",
        "ing_06",
        "pos_01",
        "pos_02",
        "pos_04",
        "lnk_01",
        "lnk_02",
        "lnk_03",
        "lnk_04",
        "lnk_05",
        "lnk_06",
        "lnk_07",
        "lnk_08",
        "prv_01",
        "prv_02",
        "prv_03",
        "ter_01",
        "ter_02",
        "ter_03",
        "ter_04",
        "ter_05",
        "ter_06",
        "ter_07",
        # val_01 and val_03 name a pytest on a Python function, and val_02 the
        # reviewer; none can leave PENDING until the contract or the rule
        # names a dbt test or an asset check for it.
        "val_01",
        "val_02",
        "val_03",
    }
)

NamedTest = tuple[str, frozenset[str]]


def contract_rule_ids(text: str) -> list[str]:
    """The rule ids of the contract's tables, in document order."""
    return RULE_ROW.findall(text)


def asset_check_rules(name: str) -> frozenset[str]:
    """The rule an asset check names: `<id>` or `<id>_<what>`."""
    match = LEADING_RULE.match(name)
    return frozenset({match.group(1)}) if match else frozenset()


def dbt_test_rules(node: dict[str, Any]) -> frozenset[str]:
    """The rules one dbt test names: by its file name if singular, by meta if generic."""
    if node["original_file_path"].endswith(".sql"):
        match = SINGULAR_RULE.match(node["name"])
        return frozenset({match.group(1)}) if match else frozenset()
    contract = node["config"].get("meta", {}).get("contract")
    if contract is None:
        return frozenset()
    return frozenset([contract] if isinstance(contract, str) else contract)


def dbt_tests(nodes: Iterable[dict[str, Any]]) -> list[NamedTest]:
    """The project's own dbt tests and the rules each names."""
    return [
        (node["unique_id"], dbt_test_rules(node))
        for node in nodes
        if node["resource_type"] == "test" and node["unique_id"].startswith("test.mesh_terrain.")
    ]


def call_name(call: ast.Call) -> str | None:
    function = call.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return None


def check_name(call: ast.Call) -> str | None:
    """The check name a call passes as a string: name=, or AssetCheckSpec's first argument."""
    candidates = [k.value for k in call.keywords if k.arg == "name"]
    if call_name(call) == "AssetCheckSpec":
        candidates += call.args[:1]
    for value in candidates:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    return None


def unnamed_asset_check(decorator: ast.expr) -> bool:
    """Whether a decorator makes an asset check that takes the function's name."""
    if isinstance(decorator, ast.Name):
        return decorator.id == "asset_check"
    return (
        isinstance(decorator, ast.Call)
        and call_name(decorator) == "asset_check"
        and check_name(decorator) is None
    )


def asset_check_names(tree: ast.AST) -> Iterator[str]:
    """Names of the asset checks a module declares by spec or by decorator."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and call_name(node) in ASSET_CHECK_CALLS:
            name = check_name(node)
            if name is not None:
                yield name
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if any(unnamed_asset_check(decorator) for decorator in node.decorator_list):
                yield node.name


def python_sources(folders: Iterable[Path], pattern: str) -> Iterator[tuple[Path, ast.AST]]:
    for folder in folders:
        for path in sorted(folder.rglob(pattern)):
            yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def asset_checks() -> list[NamedTest]:
    folders = [REPO_ROOT / package for package in ASSET_CHECK_PACKAGES]
    return [
        (f"asset check {name} ({path.relative_to(REPO_ROOT).as_posix()})", asset_check_rules(name))
        for path, tree in python_sources(folders, "*.py")
        for name in asset_check_names(tree)
    ]


def all_named_tests() -> list[NamedTest]:
    return [*dbt_tests(dbt_nodes(DEVELOPMENT_TARGET)), *asset_checks()]


def contract_problems(
    rule_ids: list[str], tests: Iterable[NamedTest], pending: frozenset[str]
) -> list[str]:
    """What is wrong between the contract's rules, the tests that name them and PENDING."""
    named = frozenset().union(*(rules for _, rules in tests))
    problems = [
        f"{rule} has no test named for it" for rule in rule_ids if rule not in pending | named
    ]
    problems += [
        f"{rule} has a test now; remove it from PENDING"
        for rule in rule_ids
        if rule in pending & named
    ]
    problems += [
        f"{rule} is on PENDING but not in the contract"
        for rule in sorted(pending)
        if rule not in rule_ids
    ]
    return problems


def without_rule(tests: Iterable[NamedTest], rule: str) -> list[NamedTest]:
    """The tests as they would be with every test naming the rule removed."""
    return [(label, rules) for label, rules in tests if rule not in rules]


def test_the_parser_reads_only_the_id_column() -> None:
    text = (
        "| Id | Rule | Enforced by |\n"
        "| --- | --- | --- |\n"
        "| `lnk_03` | Relayer and gateway differ, see `lnk_01`. | singular test |\n"
        "| `ing_06` | Ordering uses `received_at`. | reviewer checks |\n"
        "Prose that cites `pos_01` is not a rule.\n"
    )
    assert contract_rule_ids(text) == ["lnk_03", "ing_06"]


def test_the_contract_has_unique_ids_in_every_section() -> None:
    rule_ids = contract_rule_ids(CONTRACT.read_text(encoding="utf-8"))
    assert len(rule_ids) == len(set(rule_ids))
    assert {rule.split("_")[0] for rule in rule_ids} == {
        "ing",
        "snp",
        "pos",
        "lnk",
        "prv",
        "ter",
        "val",
    }


def test_singular_dbt_tests_name_a_rule_by_file_name() -> None:
    def singular(name: str) -> dict[str, Any]:
        return {"name": name, "original_file_path": f"tests/{name}.sql", "config": {}}

    assert dbt_test_rules(singular("assert_lnk_03")) == {"lnk_03"}
    assert dbt_test_rules(singular("assert_lnk_03_relayer_is_not_gateway")) == {"lnk_03"}
    assert dbt_test_rules(singular("assert_lnk_031")) == frozenset()
    assert dbt_test_rules(singular("assert_msa_boundary_is_valid")) == frozenset()


def test_generic_dbt_tests_name_rules_by_meta() -> None:
    def generic(meta: dict[str, Any]) -> dict[str, Any]:
        path = "models/core/core.yml"
        return {"name": "unique_x", "original_file_path": path, "config": {"meta": meta}}

    assert dbt_test_rules(generic({"contract": "lnk_08"})) == {"lnk_08"}
    assert dbt_test_rules(generic({"contract": ["lnk_05", "lnk_06"]})) == {"lnk_05", "lnk_06"}
    assert dbt_test_rules(generic({})) == frozenset()


def test_asset_checks_name_a_rule_by_leading_id() -> None:
    assert asset_check_rules("pos_03_not_both_zero") == {"pos_03"}
    assert asset_check_rules("lnk_03") == {"lnk_03"}
    assert asset_check_rules("decode_rate_ing_04") == frozenset()
    assert asset_check_rules("lnk_031_x") == frozenset()


def test_a_pytest_name_does_not_count_as_a_rules_test() -> None:
    labels = [label for label, _ in all_named_tests()]
    assert not [label for label in labels if "::test_" in label]


def test_pending_only_shrinks() -> None:
    assert PENDING <= PENDING_AT_B1


def test_asset_checks_are_read_from_specs_and_decorators() -> None:
    tree = ast.parse(
        "AssetCheckSpec('ing_07_second_load', asset=['raw'])\n"
        "@asset_check(asset='x', name='pos_03_zero')\n"
        "def ignored(): ...\n"
        "@asset_check('ter_01_asset_key_not_a_name')\n"
        "def lnk_04_three_packets(): ...\n"
        "@asset_check\n"
        "def lnk_08_grain(): ...\n"
    )
    assert sorted(asset_check_names(tree)) == [
        "ing_07_second_load",
        "lnk_04_three_packets",
        "lnk_08_grain",
        "pos_03_zero",
    ]


def test_every_contract_rule_has_a_test_or_is_pending() -> None:
    rule_ids = contract_rule_ids(CONTRACT.read_text(encoding="utf-8"))
    assert contract_problems(rule_ids, all_named_tests(), PENDING) == []


def test_removing_a_rules_tests_fails_the_check() -> None:
    rule_ids = contract_rule_ids(CONTRACT.read_text(encoding="utf-8"))
    tests = all_named_tests()
    tested = [rule for rule in rule_ids if rule not in PENDING]
    assert tested, "no rule has a test yet, so removal cannot be checked"
    for rule in tested:
        problems = contract_problems(rule_ids, without_rule(tests, rule), PENDING)
        assert problems == [f"{rule} has no test named for it"]


def test_a_pending_rule_that_gains_a_test_fails_the_check() -> None:
    rule_ids = contract_rule_ids(CONTRACT.read_text(encoding="utf-8"))
    rule = sorted(PENDING)[0]
    tests = [*all_named_tests(), ("tests/new.py::test_new", frozenset({rule}))]
    assert contract_problems(rule_ids, tests, PENDING) == [
        f"{rule} has a test now; remove it from PENDING"
    ]


def test_a_pending_rule_missing_from_the_contract_fails_the_check() -> None:
    problems = contract_problems(["lnk_03"], [], frozenset({"lnk_03", "lnk_99"}))
    assert problems == ["lnk_99 is on PENDING but not in the contract"]
