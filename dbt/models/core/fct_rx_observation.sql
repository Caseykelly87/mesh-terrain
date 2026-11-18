{{
    config(
        enabled=reads_raw(),
        materialized='incremental',
        unique_key='delivery_key',
        incremental_strategy='delete+insert'
    )
}}

-- One row per receipt that passes lnk_01, lnk_03 and lnk_06: a radio receipt
-- whose last relayer resolved to exactly one known node other than the
-- gateway, with both readings in range. The relayer is tx_node_hash and the
-- gateway gateway_node_hash, the names lnk_08 gives the link-hour key.
--
-- Incremental: a run adds the receipts received after the latest received_at
-- already here, and every receipt of a snapshot not yet here. Resolution
-- reads only the 24 hours before a receipt, so later data never changes a
-- receipt already here; a new snapshot that adds deliveries before a receipt
-- already here needs --full-refresh.

with receipts as (
    select *
    from {{ ref('int_rx_resolution') }}
    where drop_rule is null
    {% if is_incremental() %}
        and (
            received_at > (select max(received_at) from {{ this }})
            or snapshot_id not in (select distinct snapshot_id from {{ this }})
        )
    {% endif %}
)

select
    delivery_key,
    snapshot_id,
    received_at,
    hour,
    relayer_pseudonym as tx_node_hash,
    gateway_pseudonym as gateway_node_hash,
    link_source,
    origin_pseudonym,
    packet_id,
    relay_node,
    rx_snr,
    rx_rssi,
    modem_preset,
    seen_public,
    seen_meshstl
from receipts
