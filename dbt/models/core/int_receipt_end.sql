{{ config(enabled=reads_raw(), materialized='ephemeral') }}

-- Each end of each receipt in fct_rx_observation with its own box under
-- lnk_02, built from the cells int_receipt_end_cell marks as covered: the
-- stationary box or the fallback cell. An end with no box has no position for
-- the receipt, which lnk_02 drops.

with box_cells as (
    select *
    from {{ ref('int_receipt_end_cell') }}
    where is_box_cell
),

boxes as (
    {{ box_from_cells('box_cells', ['receipt_key', 'end_role']) }}
),

stationarity as (
    select
        receipt_key,
        end_role,
        bool_or(stationary) as stationary
    from {{ ref('int_receipt_end_cell') }}
    group by receipt_key, end_role
)

select
    receipts.delivery_key as receipt_key,
    roles.end_role,
    case roles.end_role
        when 'relayer' then receipts.tx_node_hash
        else receipts.gateway_node_hash
    end as node_pseudonym,
    receipts.received_at,
    receipts.hour,
    receipts.tx_node_hash,
    receipts.gateway_node_hash,
    coalesce(stationarity.stationary, false) as stationary,
    boxes.south_i is not null as has_box,
    boxes.south_i,
    boxes.north_i,
    boxes.west_i,
    boxes.east_i,
    boxes.merged,
    boxes.precision_bits,
    boxes.half_width_ns_m,
    boxes.half_width_ew_m
from {{ ref('fct_rx_observation') }} as receipts
cross join (values ('relayer'), ('gateway')) as roles (end_role)
left join stationarity
    on
        stationarity.receipt_key = receipts.delivery_key
        and stationarity.end_role = roles.end_role
left join boxes
    on
        boxes.receipt_key = receipts.delivery_key
        and boxes.end_role = roles.end_role
