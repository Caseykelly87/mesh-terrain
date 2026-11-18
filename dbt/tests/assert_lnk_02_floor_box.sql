{{ config(enabled=fixture_build()) }}

-- lnk_02's box around merged fixes: two 32-bit fixes 8 m apart inside one
-- 16-bit cell give the rectangle around them, with its position_uncertainty_m
-- (c16); a fix on a cell's lower edge is held by it, one on its upper edge
-- only touches it (c17).
-- Returns each mismatch.

{{ link_rules_mismatches(['c16', 'c17']) }}
