{{ config(enabled=fixture_build()) }}

-- lnk_01's direct-hop clause: relay_node equal to the sender's own byte
-- names the sender, even where the gateway heard another node with that byte
-- in the 24 hours, which would leave two candidates (staging case s05).
-- Returns each mismatch.

{{ link_rules_mismatches(['s05']) }}
