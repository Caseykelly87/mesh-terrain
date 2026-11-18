{{ config(enabled=fixture_build()) }}

-- lnk_02's stationarity and fallback on the link-rules fixture: repeated and
-- nested cells, either broker and source, touching cells, the outlier fix,
-- the fallback's nearest choice and its 30-minute and 7-day edges, positions
-- after the receipt and at its instant, and the positions the rule does not
-- read (c11 to c15, c20, c25 to c32, c44, c46, c50).
-- Returns each mismatch.

{{ link_rules_mismatches(['c11', 'c12', 'c13', 'c14', 'c15', 'c20', 'c25', 'c26', 'c27', 'c28', 'c29', 'c30', 'c31', 'c32', 'c44', 'c46', 'c50']) }}
