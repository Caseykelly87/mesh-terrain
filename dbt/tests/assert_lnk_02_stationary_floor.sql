{{ config(enabled=fixture_build()) }}

-- lnk_02's floor STATIONARY_FLOOR_M on the link-rules fixture: two 32-bit
-- fixes 8 m apart are the same place (c16), and the nearest grid pairs at
-- 39.996 m and 40.007 m fall either side of it (c18).
-- Returns each mismatch.

{{ link_rules_mismatches(['c16', 'c18']) }}
