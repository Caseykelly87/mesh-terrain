{{ config(enabled=reads_raw()) }}

-- lnk_04 on every link-hour: at least 3 packets, no more than the receipts
-- fct_rx_observation holds for that relayer, gateway and hour, and every
-- counted receipt inside the clock hour, half-open. It reads the two tables
-- only, so a real build does not rebuild the ephemeral link steps for it;
-- the per-receipt order is compared on the fixture by
-- assert_lnk_04_fixture_receipts and assert_lnk_04_fixture_link_hours.
-- Returns each link-hour that breaks it.

with receipts as (
    select
        tx_node_hash,
        gateway_node_hash,
        hour,
        count(*) as receipts
    from {{ ref('fct_rx_observation') }}
    group by tx_node_hash, gateway_node_hash, hour
)

select
    windows.tx_node_hash,
    windows.gateway_node_hash,
    windows.hour
from {{ ref('int_link_window') }} as windows
left join receipts
    on
        receipts.tx_node_hash = windows.tx_node_hash
        and receipts.gateway_node_hash = windows.gateway_node_hash
        and receipts.hour = windows.hour
where
    windows.packet_count < 3
    or windows.packet_count > coalesce(receipts.receipts, 0)
    or {{ link_hour('windows.first_received_at') }} <> windows.hour
    or {{ link_hour('windows.last_received_at') }} <> windows.hour
