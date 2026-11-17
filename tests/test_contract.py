"""Every rule in docs/data-quality-contract.md has a test named for it.

The contract's opening line asks for "a dbt test or a Dagster asset check with
the same identifier", and its naming line (ADR 0007) says how each carries it.
So a test names the rule `lnk_03` in one of three ways:

- a dbt singular test file `assert_lnk_03.sql` or `assert_lnk_03_<what>.sql`;
- a dbt generic test in model YAML with `config: {meta: {contract: lnk_03}}`, or a
  list of ids when one test enforces several rules;
- a Dagster asset check named `lnk_03` or `lnk_03_<what>`, as
  `ing_04_decode_rate` names `ing_04` (Casey, issue #55, D11; ADR 0007).

Nothing else counts, a pytest function included, since the opening line names
only those two kinds. Rules that no test names yet are on PENDING. The check
fails when a rule off the list has no test, and when a rule on the list has
one, so a rule leaves the list in the same change that adds its test. PENDING
may only hold rules in PENDING_AT_B1, so it only shrinks. A new contract rule
enters both PENDING_AT_B1 and PENDING in the pull request that adds it to the
contract, unless it lands with its test.

A rule the contract leaves to the reviewer alone is on REVIEW_ONLY, never on
PENDING (Casey, issue #55, D12): it needs no named test, its "Enforced by"
cell names no dbt test or asset check, and the reviewer's checklist in
`.claude/agents/reviewer.md` has a line that opens with its id. The check
needs one named test per rule, not every item of the contract's "Enforced by"
column; the reviewer checks that.
"""

import ast
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from dbt_listing import DEVELOPMENT_TARGET, REPO_ROOT, dbt_nodes

CONTRACT = REPO_ROOT / "docs" / "data-quality-contract.md"
REVIEWER = REPO_ROOT / ".claude" / "agents" / "reviewer.md"
RULE_ROW = re.compile(r"^\| `([a-z]{3}_\d{2})` \|", re.MULTILINE)
CELL_BORDER = re.compile(r"(?<!\\)\|")
LEADING_RULE = re.compile(r"^([a-z]{3}_\d{2})(?:_|$)")
SINGULAR_RULE = re.compile(r"^assert_([a-z]{3}_\d{2})(?:_|$)")
CHECKLIST_RULE = re.compile(r"^\s*(?:\d+\.|[-*])\s+`([a-z]{3}_\d{2})`", re.MULTILINE)
NAMES_A_TEST = re.compile(
    r"\b(?:tests?|asset checks?|accepted_values|not_null|unique|relationships?)\b",
    re.IGNORECASE,
)
ASSET_CHECK_PACKAGES = ("ingester", "pipeline", "engine", "terrain")
ASSET_CHECK_CALLS = {"AssetCheckSpec", "asset_check"}

# The rules pending when this test was written (issue #55, B1, 2026-11-17):
# all 33, since no dbt test or asset check carried a rule id exactly then.
# PENDING must stay a subset of it. Edit it only to add a new contract rule,
# in the pull request that adds the rule, unless the rule lands with its test.
PENDING_AT_B1 = frozenset(
    {
        *("ing_01", "ing_02", "ing_03", "ing_04", "ing_05", "ing_06", "ing_07"),
        *("snp_01",),
        *("pos_01", "pos_02", "pos_03", "pos_04"),
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
        # (allowlist) have pytest tests, which the parser does not count.
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
        # val_01 leaves with its dbt singular test on the hold-out split, once
        # the validation marts exist (Phase 4; Casey, issue #55, D12), and
        # val_03 with its singular dbt test.
        "val_01",
        "val_03",
    }
)

# Rules the contract enforces by the reviewer alone (Casey, issue #55, D12).
REVIEW_ONLY = frozenset({"val_02"})

NamedTest = tuple[str, frozenset[str]]


def contract_rule_ids(text: str) -> list[str]:
    """The rule ids of the contract's tables, in document order."""
    return RULE_ROW.findall(text)


def enforced_by(text: str) -> dict[str, str]:
    """Each rule's "Enforced by" cell, the last cell of its row; `\\|` stays in a cell."""
    cells: dict[str, str] = {}
    for line in text.splitlines():
        if match := RULE_ROW.match(line):
            cells[match.group(1)] = CELL_BORDER.split(line.strip())[-2].strip()
    return cells


def checklist_rules(text: str) -> frozenset[str]:
    """The rule ids that open a numbered or bulleted line, as in the reviewer's checklist."""
    return frozenset(CHECKLIST_RULE.findall(text))


def asset_check_rules(name: str) -> frozenset[str]:
    """The rule an asset check names: `<id>` or `<id>_<what>` (ADR 0007)."""
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
    rule_ids: list[str],
    tests: Iterable[NamedTest],
    pending: frozenset[str],
    review_only: frozenset[str] = frozenset(),
) -> list[str]:
    """What is wrong between the contract's rules, the tests that name them and the lists."""
    named = frozenset().union(*(rules for _, rules in tests))
    covered = pending | review_only | named
    problems = [f"{rule} has no test named for it" for rule in rule_ids if rule not in covered]
    for listed, label in ((pending, "PENDING"), (review_only, "REVIEW_ONLY")):
        problems += [
            f"{rule} has a test now; remove it from {label}"
            for rule in rule_ids
            if rule in listed & named
        ]
        problems += [
            f"{rule} is on {label} but not in the contract"
            for rule in sorted(listed)
            if rule not in rule_ids
        ]
    return problems


def review_only_problems(
    cells: dict[str, str],
    checklist: frozenset[str],
    review_only: frozenset[str],
    pending: frozenset[str],
) -> list[str]:
    """What is wrong with REVIEW_ONLY against PENDING, the contract and the reviewer's checklist."""
    on_both = sorted(review_only & pending)
    problems = [f"{rule} is on both PENDING and REVIEW_ONLY" for rule in on_both]
    for rule in sorted(review_only):
        cell = cells.get(rule, "")
        if NAMES_A_TEST.search(cell):
            problems.append(f"{rule} is on REVIEW_ONLY but the contract names a test: {cell}")
        if rule not in checklist:
            problems.append(f"{rule} is on REVIEW_ONLY but has no line in the reviewer's checklist")
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


def test_the_parser_reads_the_enforced_by_cell() -> None:
    text = (
        "| Id | Rule | Enforced by |\n"
        "| --- | --- | --- |\n"
        "| `ing_01` | A key `a \\|\\| b`. | unique test \\| known-answer test |\n"
        "| `val_02` | Numbers name their source. | reviewer checks |\n"
    )
    assert enforced_by(text) == {
        "ing_01": "unique test \\| known-answer test",
        "val_02": "reviewer checks",
    }


def test_the_parser_reads_rule_ids_that_open_a_checklist_line() -> None:
    text = (
        "10. Decided without Casey. Each item.\n"
        "11. `val_02`: every headline number names its source.\n"
        "- `val_09`: a bulleted line.\n"
        "Prose that cites `val_03` is not a checklist line.\n"
        "12. Text, then `val_04`.\n"
    )
    assert checklist_rules(text) == {"val_02", "val_09"}


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


def test_asset_checks_name_a_rule_by_its_id_alone_or_followed_by_a_description() -> None:
    assert asset_check_rules("pos_03") == {"pos_03"}
    assert asset_check_rules("pos_03_not_both_zero") == {"pos_03"}
    assert asset_check_rules("decode_rate_ing_04") == frozenset()
    assert asset_check_rules("lnk_031_x") == frozenset()
    assert asset_check_rules("pos_03x") == frozenset()


def test_the_raw_asset_checks_name_their_five_rules() -> None:
    named = {
        rule
        for label, rules in asset_checks()
        if label.endswith("(ingester/assets.py)")
        for rule in rules
    }
    assert named == {"ing_03", "ing_04", "ing_07", "snp_01", "pos_03"}


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


def test_every_contract_rule_has_a_test_or_is_listed() -> None:
    rule_ids = contract_rule_ids(CONTRACT.read_text(encoding="utf-8"))
    assert contract_problems(rule_ids, all_named_tests(), PENDING, REVIEW_ONLY) == []


def test_every_review_only_rule_is_left_to_the_reviewer_alone() -> None:
    cells = enforced_by(CONTRACT.read_text(encoding="utf-8"))
    checklist = checklist_rules(REVIEWER.read_text(encoding="utf-8"))
    assert review_only_problems(cells, checklist, REVIEW_ONLY, PENDING) == []


def test_a_review_only_rule_that_breaks_a_condition_fails_the_check() -> None:
    cells = {
        "val_02": "reviewer checks",
        "lnk_03": "singular test",
        "ing_04": "Dagster asset check `decode_rate` with warn severity",
        "pos_02": "accepted_values on the flag",
    }
    review_only = frozenset({"val_02", "lnk_03", "ing_04", "pos_02"})
    on_both = frozenset({"val_02"})
    problems = review_only_problems(cells, frozenset({"val_02"}), review_only, on_both)
    assert problems == [
        "val_02 is on both PENDING and REVIEW_ONLY",
        "ing_04 is on REVIEW_ONLY but the contract names a test: "
        "Dagster asset check `decode_rate` with warn severity",
        "ing_04 is on REVIEW_ONLY but has no line in the reviewer's checklist",
        "lnk_03 is on REVIEW_ONLY but the contract names a test: singular test",
        "lnk_03 is on REVIEW_ONLY but has no line in the reviewer's checklist",
        "pos_02 is on REVIEW_ONLY but the contract names a test: accepted_values on the flag",
        "pos_02 is on REVIEW_ONLY but has no line in the reviewer's checklist",
    ]


def test_a_review_only_rule_needs_no_test_and_fails_when_it_gains_one() -> None:
    review_only = frozenset({"val_02"})
    assert contract_problems(["val_02"], [], frozenset(), review_only) == []
    tests = [("dbt/tests/assert_val_02.sql", frozenset({"val_02"}))]
    assert contract_problems(["val_02"], tests, frozenset(), review_only) == [
        "val_02 has a test now; remove it from REVIEW_ONLY"
    ]
    assert contract_problems(["lnk_03"], [], frozenset(), review_only) == [
        "lnk_03 has no test named for it",
        "val_02 is on REVIEW_ONLY but not in the contract",
    ]


def test_removing_a_rules_tests_fails_the_check() -> None:
    # Most rules are listed today, so each one is given a test here first.
    rule_ids = contract_rule_ids(CONTRACT.read_text(encoding="utf-8"))
    tests = all_named_tests()
    for rule in rule_ids:
        with_test = [*tests, (f"dbt/tests/assert_{rule}.sql", frozenset({rule}))]
        pending = PENDING - {rule}
        review_only = REVIEW_ONLY - {rule}
        assert contract_problems(rule_ids, with_test, pending, review_only) == []
        problems = contract_problems(rule_ids, without_rule(with_test, rule), pending, review_only)
        assert problems == [f"{rule} has no test named for it"]


def test_a_pending_rule_that_gains_a_test_fails_the_check() -> None:
    rule_ids = contract_rule_ids(CONTRACT.read_text(encoding="utf-8"))
    rule = sorted(PENDING)[0]
    tests = [*all_named_tests(), ("dbt/tests/assert_new.sql", frozenset({rule}))]
    assert contract_problems(rule_ids, tests, PENDING, REVIEW_ONLY) == [
        f"{rule} has a test now; remove it from PENDING"
    ]


def test_a_pending_rule_missing_from_the_contract_fails_the_check() -> None:
    problems = contract_problems(["lnk_03"], [], frozenset({"lnk_03", "lnk_99"}))
    assert problems == ["lnk_99 is on PENDING but not in the contract"]
