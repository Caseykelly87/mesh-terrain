{{ config(enabled=fixture_build()) }}

-- lnk_01's direct hops dropped because the gateway's NeighborInfo resolves
-- the byte to another node, the count named lnk_01_direct_hop_contradicted
-- (Casey, PR #66 comment 6968763406, Q4): 3 receipts across the link-rules
-- fixture, all in c48; a relayed receipt whose two methods disagree (c06) is
-- not one of them. Returns a row when the count differs.

select
    'lnk_01_direct_hop_contradicted: ' || count(*) as item,
    'the fixture counts 3, all in c48' as mismatch
from {{ ref('int_rx_resolution') }}
where
    direct_hop_contradicted
    and {{ link_rules_cases_only('delivery_key') }}
having count(*) <> 3 or count(*) filter (where delivery_key like 'c48-%') <> 3
