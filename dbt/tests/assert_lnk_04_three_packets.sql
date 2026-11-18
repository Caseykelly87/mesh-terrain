{{ config(enabled=reads_raw()) }}

-- lnk_04: every link-hour has at least 3 packets, counted after lnk_05's
-- per-receipt test, and its packet_count is the number of its counted
-- receipts, each in the clock hour that holds its received_at. Returns each
-- link-hour that breaks it.

with counted as (
    select
        tx_node_hash,
        gateway_node_hash,
        hour,
        count(*) as receipts
    from {{ ref('int_link_receipt') }}
    where
        drop_rule is null
        and hour = {{ link_hour('received_at') }}
    group by tx_node_hash, gateway_node_hash, hour
)

select
    windows.tx_node_hash,
    windows.gateway_node_hash,
    windows.hour
from {{ ref('int_link_window') }} as windows
left join counted
    on
        counted.tx_node_hash = windows.tx_node_hash
        and counted.gateway_node_hash = windows.gateway_node_hash
        and counted.hour = windows.hour
where
    windows.packet_count < 3
    or windows.packet_count is distinct from counted.receipts
