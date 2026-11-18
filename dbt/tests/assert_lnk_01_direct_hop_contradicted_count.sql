{{
    config(
        enabled=reads_raw(),
        severity='warn',
        warn_if='>= 0'
    )
}}

-- lnk_01_direct_hop_contradicted, reported on every build (Casey, PR #72,
-- Q1: "A test that warns with the Q4 count on every build now, and a row in
-- mart_data_quality once that mart exists"): the direct hops lnk_01 drops
-- because the gateway's NeighborInfo resolves the byte to another node. It
-- warns with the count every time, 0 included, and never fails a build; the
-- log shows only the count.

select delivery_key
from {{ ref('int_rx_resolution') }}
where direct_hop_contradicted
