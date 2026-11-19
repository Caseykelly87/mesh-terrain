{{ config(enabled=fixture_build()) }}

-- lnk_02's fallback tie at one instant goes to the broadcast whose first
-- copy has the smaller delivery key (Casey, PR #72, his answer to PR #79,
-- Q1; staging case s14). Returns each mismatch.

{{ link_rules_mismatches(['s14']) }}
