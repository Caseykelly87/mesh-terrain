{{ config(enabled=fixture_build()) }}

-- lnk_08 on the link-rules fixture: one row per relayer, gateway and hour,
-- and the clock hour is half-open (c39, c47); copies of one packet count
-- once (c33, c38, c40).
-- Returns each mismatch.

{{ link_rules_mismatches(['c33', 'c38', 'c39', 'c40', 'c47']) }}
