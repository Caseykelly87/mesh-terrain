{{ config(enabled=fixture_build()) }}

-- lnk_04's order on the link-rules fixture, per link-hour: every
-- (relayer, gateway, hour) with a counted receipt is kept or dropped by the
-- hand-worked rule, with its counted receipts, and a kept hour's medians,
-- distance, boxes, precise flags and position_uncertainty_m match.
-- Returns each mismatch.

{{ link_rules_hour_mismatches() }}
