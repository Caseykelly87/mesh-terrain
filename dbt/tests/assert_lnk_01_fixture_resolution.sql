{{ config(enabled=fixture_build()) }}

-- lnk_01 on the link-rules fixture: direct hops, one candidate, two
-- candidates, the 24-hour window and its edges, radio and MQTT senders,
-- NeighborInfo alone, agreeing and contradicting, low byte 0, and reports
-- counted together (c02 to c10, c43, c45, c48, c49, c51).
-- Returns each mismatch.

{{ link_rules_mismatches(['c02', 'c03', 'c04', 'c05', 'c06', 'c07', 'c08', 'c09', 'c10', 'c43', 'c45', 'c48', 'c49', 'c51']) }}
