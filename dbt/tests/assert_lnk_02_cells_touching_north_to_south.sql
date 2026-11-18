{{ config(enabled=fixture_build()) }}

-- lnk_02: two cells that share a latitude edge only touch and do not
-- overlap, so their node has moved (staging case s08; c14 tests longitude).
-- Returns each mismatch.

{{ link_rules_mismatches(['s08']) }}
