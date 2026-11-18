{{ config(enabled=fixture_build()) }}

-- The box's precise flag reads only the positions lnk_02 read for that end
-- in that hour (PLAN, Data model; Casey, issue #55, D7; PR #66, Q1): c41
-- for an hour box, c44 for the fallback's look-back.
-- Returns each mismatch.

{{ link_rules_mismatches(['c41', 'c44']) }}
