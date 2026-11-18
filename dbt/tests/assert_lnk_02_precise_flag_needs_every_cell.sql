{{ config(enabled=fixture_build()) }}

-- The precise flag of an end off the hour box needs every cell its box covers
-- to qualify under pos_04, not one of them (PLAN, Data model; D7; staging
-- case s09).
-- Returns each mismatch.

{{ link_rules_mismatches(['s09']) }}
