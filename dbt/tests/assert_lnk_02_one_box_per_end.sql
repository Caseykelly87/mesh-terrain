{{ config(enabled=fixture_build()) }}

-- lnk_02's one-box test on the link-rules fixture: a link-hour whose counted
-- receipts used two boxes at the relayer (c24) or at the gateway (c42) is
-- dropped, and the fixture drops exactly those 2 hours by it, the count
-- mart_data_quality will report (Casey, issue #55, Q6 and Q9). Returns each
-- mismatch, and a row when the count is not 2.

{{ link_rules_mismatches(['c24', 'c42']) }}

union all

select
    'link-hours dropped by lnk_02: ' || count(*) as item,
    'the fixture drops 2' as mismatch
from {{ ref('int_link_hour') }}
where
    drop_rule = 'lnk_02'
    and {{ case_filter('tx_node_hash', none) }}
having count(*) <> 2
