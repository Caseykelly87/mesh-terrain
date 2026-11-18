{{ config(enabled=reads_raw(), materialized='ephemeral') }}

-- Every (relayer, gateway, hour) with at least one counted receipt, and
-- whether lnk_04's order keeps it: one box per end over the counted receipts,
-- all four edges equal, or the hour is dropped by lnk_02 (Casey, issue #55,
-- Q6 and Q9); then at least 3 packets (lnk_04); then a median distance of at
-- least 500 m (lnk_05). drop_rule is null for a kept hour. Medians are
-- percentile_cont(0.5) over the counted receipts (Q8). An end's precise flag
-- is its hour box's when it has one; otherwise every cell its box covers must
-- have a qualifying delivery among the positions the counted receipts read
-- (PLAN, Data model; D7; PR #66, Q1).

{% set key = ['tx_node_hash', 'gateway_node_hash', 'hour'] %}
{% set box_columns = ['south_i', 'north_i', 'west_i', 'east_i', 'merged', 'precision_bits', 'half_width_ns_m', 'half_width_ew_m'] %}

with counted as (
    select *
    from {{ ref('int_link_receipt') }}
    where drop_rule is null
),

hours as (
    select
        {{ key | join(', ') }},
        count(*) as packet_count,
        min(received_at) as first_received_at,
        max(received_at) as last_received_at,
        count(distinct (relayer_south_i, relayer_north_i, relayer_west_i, relayer_east_i))
            as relayer_boxes,
        count(distinct (gateway_south_i, gateway_north_i, gateway_west_i, gateway_east_i))
            as gateway_boxes,
        percentile_cont(0.5) within group (order by rx_snr) as median_snr_db,
        percentile_cont(0.5) within group (order by rx_rssi) as median_rssi_dbm,
        percentile_cont(0.5) within group (order by distance_m) as distance_m,
        case when count(distinct modem_preset) = 1 and count(modem_preset) = count(*)
            then min(modem_preset) end as modem_preset,
        bool_and(relayer_on_hour_box) as relayer_on_hour_box,
        bool_and(gateway_on_hour_box) as gateway_on_hour_box,
        {% for column in box_columns -%}
        {% set aggregate = 'bool_and' if column == 'merged' else 'min' -%}
        {{ aggregate }}(relayer_{{ column }}) as relayer_{{ column }},
        {{ aggregate }}(gateway_{{ column }}) as gateway_{{ column }}{{ ',' if not loop.last }}
        {% endfor %}
    from counted
    group by {{ key | join(', ') }}
),

read_cells as (
    select
        {% for column in key %}counted.{{ column }}, {% endfor %}
        cells.end_role,
        cells.south_i,
        cells.north_i,
        cells.west_i,
        cells.east_i,
        bool_or(cells.any_precise) as any_precise,
        bool_or(cells.is_box_cell) as is_box_cell
    from counted
    inner join {{ ref('int_receipt_end_cell') }} as cells
        on cells.receipt_key = counted.delivery_key
    group by 1, 2, 3, 4, 5, 6, 7, 8
),

read_precise as (
    select
        {{ key | join(', ') }},
        bool_and(any_precise) filter (where end_role = 'relayer') as relayer_precise_source,
        bool_and(any_precise) filter (where end_role = 'gateway') as gateway_precise_source
    from read_cells
    where is_box_cell
    group by {{ key | join(', ') }}
),

hour_box_precise as (
    select
        {{ key | join(', ') }},
        bool_or(precise_source) filter (where end_role = 'relayer') as relayer_precise_source,
        bool_or(precise_source) filter (where end_role = 'gateway') as gateway_precise_source
    from {{ ref('int_link_hour_box') }}
    group by {{ key | join(', ') }}
)

select
    hours.*,
    case
        when hours.relayer_on_hour_box then hour_box_precise.relayer_precise_source
        else read_precise.relayer_precise_source
    end as relayer_precise_source,
    case
        when hours.gateway_on_hour_box then hour_box_precise.gateway_precise_source
        else read_precise.gateway_precise_source
    end as gateway_precise_source,
    greatest(hours.relayer_half_width_ns_m, hours.relayer_half_width_ew_m)
    + greatest(hours.gateway_half_width_ns_m, hours.gateway_half_width_ew_m)
        as position_uncertainty_m,
    case
        when hours.relayer_boxes > 1 or hours.gateway_boxes > 1 then 'lnk_02'
        when hours.packet_count < 3 then 'lnk_04'
        when hours.distance_m < 500 then 'lnk_05'
    end as drop_rule
from hours
left join read_precise
    on
        read_precise.tx_node_hash = hours.tx_node_hash
        and read_precise.gateway_node_hash = hours.gateway_node_hash
        and read_precise.hour = hours.hour
left join hour_box_precise
    on
        hour_box_precise.tx_node_hash = hours.tx_node_hash
        and hour_box_precise.gateway_node_hash = hours.gateway_node_hash
        and hour_box_precise.hour = hours.hour
