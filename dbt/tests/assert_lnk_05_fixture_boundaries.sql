{{ config(enabled=fixture_build()) }}

-- lnk_05 on the link-rules fixture: the per-receipt test before the median
-- (c23), the 500 m and 60 000 m limits, both included (c34, c35), and boxes
-- that overlap (c36).
-- Returns each mismatch.

{{ link_rules_mismatches(['c23', 'c34', 'c35', 'c36']) }}
