{{ config(enabled=fixture_build()) }}

-- lnk_02's hour box reads the positions from 7 days before the first receipt
-- of the hour, not the last (staging case s11).
-- Returns each mismatch.

{{ link_rules_mismatches(['s11']) }}
