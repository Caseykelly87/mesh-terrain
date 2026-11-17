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
