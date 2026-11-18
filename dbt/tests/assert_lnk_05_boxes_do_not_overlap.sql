{{ config(enabled=reads_raw()) }}

-- lnk_05 on every link-hour: the relayer and gateway boxes do not overlap by
-- lnk_02's integer test (Casey, issue #55, Q2). It reads int_link_window
-- only, so a real build does not rebuild the ephemeral link steps for it;
-- the per-receipt test before the median is compared on the fixture by
-- assert_lnk_05_fixture_boundaries and assert_lnk_05_hour_box, and the
-- distance range by the range test on distance_m. Returns each row that
-- breaks it.

select
    tx_node_hash,
    gateway_node_hash,
    hour
from {{ ref('int_link_window') }}
where {{ boxes_overlap('relayer_', 'gateway_') }}
