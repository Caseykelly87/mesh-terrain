{{ config(enabled=fixture_build()) }}

-- lnk_06 on the link-rules fixture: RSSI in [-150, 0) dBm and SNR in
-- [-30, 20] dB, both present; RSSI 0 and an SNR that is not a number are
-- missing (c37).
-- Returns each mismatch.

{{ link_rules_mismatches(['c37']) }}
