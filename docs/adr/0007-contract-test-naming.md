# ADR 0007: Contract tests may add a short description after the rule id

Date: 2026-11-17
Status: accepted
Proposed by: warehouse-eng (PR #60)
Decided by: Casey

## Context

The data-quality contract (`docs/data-quality-contract.md`) says every rule has a dbt test or a Dagster asset check with the rule's identifier. Before this ADR, its line on names said a dbt singular test is named `assert_<identifier>`, for example `assert_lnk_03.sql`. The style guide asks for singular tests named for what must be true, and several rules need more than one singular test (the `lnk_02` wording in PR #58 names `assert_lnk_02_stationary_floor` and `assert_lnk_02_finest_box_in_hour`). `tests/test_contract.py` (PR #60, issue #55, unit B1) accepted `assert_<identifier>_<what>` as a reading of that line, and the reviewer flagged that the reading went beyond the line.

Casey, issue #55 comment 6960283869, item 9, verbatim: "9. Test naming: if the reviewer finds assert_<rule>_<what> goes beyond the contract's line 5, amend line 5 in #60 to allow a descriptive suffix after the rule id, with a short ADR note. Do not rely on a reading of the rule."

## Decision

A dbt singular test that enforces a contract rule is named `assert_<identifier>` or `assert_<identifier>_<what>`, `<what>` being a short description of the case, for example `assert_lnk_03_relayer_is_not_gateway.sql`. A generic test still names its rule with `meta: {contract: <identifier>}` in the model YAML.

## Consequences

- One rule can have several singular tests, each named for its case, and `tests/test_contract.py` counts each of them for the rule.
- The rule id is always the first thing after `assert_`, so a reader and the parser find it in the same place.

## Asset checks and review-only rules, 2026-11-17

The proposals (issue #55 comment 6961441051): "D11. Asset checks ... Proposed: extend your item 9 wording to asset checks (`<identifier>` or `<identifier>_<what>`), so the five come off pending in a follow-up." and "D12 ... Proposed: give `val_01` a dbt singular test when the validation marts exist (Phase 4), and keep `val_02` pending as a review-only rule, marked so in the test file."

Casey, issue #55 comment 6961809084, items 7 and 8, verbatim:

> 7. D11: agree. Extend the item 9 wording to asset checks, with the ADR 0007 note, and take the five checks off pending in the follow-up.
> 8. D12: val_01 as proposed. val_02: do not leave it on PENDING for good. Add a separate REVIEW_ONLY list in tests/test_contract.py, put val_02 there, and add a val_02 line to the reviewer's checklist. PENDING keeps its rule that it only shrinks.

What changes:

- A Dagster asset check that enforces a rule is named `<identifier>` or `<identifier>_<what>`, the way a singular test is named after `assert_`. The five raw checks in `ingester/assets.py` (`ing_03_reconcile`, `ing_04_decode_rate`, `ing_07_second_load`, `snp_01_content_hash`, `pos_03_not_both_zero`) name their rules without a rename, and those five rules leave `PENDING` in `tests/test_contract.py`.
- `val_01` gains a dbt singular test on the hold-out split, once the validation marts exist (Phase 4), beside its pytest on the split function. It stays on `PENDING` until the dbt test exists.
- `val_02` is enforced by the reviewer alone. It moves from `PENDING` to a separate `REVIEW_ONLY` list, and `.claude/agents/reviewer.md` has a checklist line that opens with `val_02`; the test fails when that line is missing. `PENDING` still only shrinks.

Not decided by Casey: warehouse-eng chose, in PR #62, that the test also fails when a rule is on both `PENDING` and `REVIEW_ONLY`, and when the contract's "Enforced by" cell for a `REVIEW_ONLY` rule names a dbt test or asset check, read from words such as test, asset check, `accepted_values` and `not_null`. The PR #62 review added that `REVIEW_ONLY` may only hold rules in `REVIEW_ONLY_AT_D12` (`val_02`), so a rule reaches it only through a change Casey sees. Whether a pytest can ever count as a rule's test is still open (Casey, issue #55, 2026-11-17, item 5; `docs/STATUS.md`).
