{{ config(enabled=reads_raw()) }}

-- lnk_01 on every receipt in fct_rx_observation: its kept copy is a radio
-- receipt (via_mqtt false), not the gateway's own packet, with a relay_node,
-- and its relayer is resolved with a link_source. Returns each row that
-- breaks it.

select receipts.delivery_key
from {{ ref('fct_rx_observation') }} as receipts
left join {{ ref('stg_packet') }} as packets
    on packets.delivery_key = receipts.delivery_key
where
    packets.delivery_key is null
    or packets.via_mqtt
    or packets.origin_pseudonym = packets.gateway_pseudonym
    or packets.relay_node is null
    or receipts.tx_node_hash is null
    or receipts.link_source is null
