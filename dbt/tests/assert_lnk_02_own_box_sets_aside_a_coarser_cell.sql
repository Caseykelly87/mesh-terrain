{{ config(enabled=fixture_build()) }}

-- lnk_02's stationary box at one receipt sets aside a coarser cell that
-- holds a finer position, on an end that has no hour box (staging case s06).
-- Returns each mismatch.

{{ link_rules_mismatches(['s06']) }}
