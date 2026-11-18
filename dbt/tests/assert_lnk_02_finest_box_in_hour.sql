{{ config(enabled=fixture_build()) }}

-- lnk_02's one box per end for the hour: a stationary node whose 20-bit cell
-- arrives between the hour's second and third receipts is placed at that
-- cell for all three (c21).
-- Returns each mismatch.

{{ link_rules_mismatches(['c21']) }}
