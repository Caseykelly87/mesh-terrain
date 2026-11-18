{{ config(enabled=reads_raw()) }}

-- lnk_05: no counted receipt and no link-hour has relayer and gateway boxes
-- that overlap by lnk_02's integer test (Casey, issue #55, Q2), and every
-- counted receipt is 500 m to 60 000 m long before the median is taken (Q3).
-- Returns each row that breaks it.

select delivery_key as item
from {{ ref('int_link_receipt') }}
where
    drop_rule is null
    and (
        {{ boxes_overlap('relayer_', 'gateway_') }}
        or distance_m not between 500 and 60000
    )

union all

select concat_ws(' ', tx_node_hash, gateway_node_hash, hour) as item
from {{ ref('int_link_window') }}
where {{ boxes_overlap('relayer_', 'gateway_') }}
