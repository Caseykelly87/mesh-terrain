{{ config(enabled=fixture_build()) }}

-- lnk_04's order on the link-rules fixture, per receipt: every expected
-- receipt's outcome and deciding rule, relayer, link_source, the boxes it
-- uses and its distance match the hand-worked values, and no receipt the
-- fixture does not count is counted (tests/fixtures/link_rules/README.md).
-- Returns each mismatch.

{{ link_rules_receipt_mismatches() }}
