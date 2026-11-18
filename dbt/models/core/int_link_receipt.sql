{{ config(enabled=reads_raw(), materialized='ephemeral') }}

-- Each receipt in fct_rx_observation with the box each end uses and
-- lnk_05's per-receipt test. An end on its hour's box uses that box;
-- otherwise the receipt's own (lnk_02; Casey, issue #55, D9). A receipt with
-- no box at an end is dropped by lnk_02. Its distance is the WGS84 geodesic
-- between the two box centres (ST_Distance on geography, spheroid on; Casey,
-- issue #55, Q4), and it passes when that is 500 m to 60 000 m, both
-- included, and the two boxes do not overlap (Q2). drop_rule is null for a
-- counted receipt.

{% set box_columns = ['south_i', 'north_i', 'west_i', 'east_i', 'merged', 'precision_bits', 'half_width_ns_m', 'half_width_ew_m'] %}

with used_boxes as (
    select
        own.receipt_key,
        own.end_role,
        own.has_box,
        hour_box.end_role is not null as on_hour_box,
        {% for column in box_columns -%}
        case when hour_box.end_role is not null then hour_box.{{ column }} else own.{{ column }} end
            as {{ column }}{{ ',' if not loop.last }}
        {% endfor %}
    from {{ ref('int_receipt_end') }} as own
    left join {{ ref('int_link_hour_box') }} as hour_box
        on
            hour_box.tx_node_hash = own.tx_node_hash
            and hour_box.gateway_node_hash = own.gateway_node_hash
            and hour_box.hour = own.hour
            and hour_box.end_role = own.end_role
),

paired as (
    select
        receipts.delivery_key,
        receipts.received_at,
        receipts.hour,
        receipts.tx_node_hash,
        receipts.gateway_node_hash,
        receipts.link_source,
        receipts.rx_snr,
        receipts.rx_rssi,
        receipts.modem_preset,
        relayer.has_box and gateway.has_box as has_boxes,
        relayer.on_hour_box as relayer_on_hour_box,
        gateway.on_hour_box as gateway_on_hour_box,
        {% for column in box_columns -%}
        relayer.{{ column }} as relayer_{{ column }},
        gateway.{{ column }} as gateway_{{ column }},
        {% endfor -%}
        {{ boxes_overlap('relayer.', 'gateway.') }} as boxes_overlap,
        st_distance(
            {{ box_centre('relayer.') }}::geography,
            {{ box_centre('gateway.') }}::geography
        ) as distance_m
    from {{ ref('fct_rx_observation') }} as receipts
    inner join used_boxes as relayer
        on
            relayer.receipt_key = receipts.delivery_key
            and relayer.end_role = 'relayer'
    inner join used_boxes as gateway
        on
            gateway.receipt_key = receipts.delivery_key
            and gateway.end_role = 'gateway'
)

select
    *,
    case
        when not has_boxes then 'lnk_02'
        when boxes_overlap or distance_m not between 500 and 60000 then 'lnk_05'
    end as drop_rule
from paired
