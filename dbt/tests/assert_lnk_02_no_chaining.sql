{{ config(enabled=fixture_build()) }}

-- lnk_02 is pairwise and never chained: fixes A, B and C at 0.75, 0.75 and
-- 1.5 times STATIONARY_FLOOR_M mean the node moved (c19).
-- Returns each mismatch.

{{ link_rules_mismatches(['c19']) }}
