{{ config(enabled=fixture_build()) }}

-- The link rules read the copies of one broadcast at several gateways as one
-- position, timed by its first copy and precise when any copy qualifies
-- (PR #72, Decided without Casey; staging case s12).
-- Returns each mismatch.

{{ link_rules_mismatches(['s12']) }}
