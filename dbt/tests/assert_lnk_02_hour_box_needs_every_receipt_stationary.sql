{{ config(enabled=fixture_build()) }}

-- lnk_02's hour box needs the end stationary at every receipt with a
-- position for both ends, even when its positions are pairwise the same place
-- (staging case s10).
-- Returns each mismatch.

{{ link_rules_mismatches(['s10']) }}
