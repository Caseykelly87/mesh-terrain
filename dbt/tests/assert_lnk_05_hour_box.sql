{{ config(enabled=fixture_build()) }}

-- lnk_05 uses the box each receipt uses: a receipt 510 m away on its own
-- 16-bit box and 490 m away on the hour's 20-bit box fails (c22; Casey,
-- issue #55, D9).
-- Returns each mismatch.

{{ link_rules_mismatches(['c22']) }}
