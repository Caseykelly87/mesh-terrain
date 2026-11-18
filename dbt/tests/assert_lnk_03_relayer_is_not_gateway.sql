{{ config(enabled=reads_raw()) }}

-- lnk_03: relayer and gateway are different nodes, in every receipt and every
-- link-hour. Returns each row where they are the same.

select delivery_key as item
from {{ ref('fct_rx_observation') }}
where tx_node_hash = gateway_node_hash

union all

select concat_ws(' ', tx_node_hash, gateway_node_hash, hour) as item
from {{ ref('int_link_window') }}
where tx_node_hash = gateway_node_hash
