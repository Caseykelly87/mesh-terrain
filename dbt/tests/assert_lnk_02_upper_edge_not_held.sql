{{ config(enabled=fixture_build()) }}

-- lnk_02's half-open cell: a 32-bit fix exactly on a 21-bit cell's north
-- edge, or its east edge, is not held by it, so the cell is not set aside
-- and the floor merges the two into one rectangle (staging case s07).
-- Returns each mismatch.

{{ link_rules_mismatches(['s07']) }}
