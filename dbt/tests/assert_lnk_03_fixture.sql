{{ config(enabled=fixture_build()) }}

-- lnk_03 on the link-rules fixture: a relayer that resolves to the gateway
-- itself is dropped (c10).
-- Returns each mismatch.

{{ link_rules_mismatches(['c10']) }}
