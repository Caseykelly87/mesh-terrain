{{ config(enabled=reads_raw(), materialized='ephemeral') }}

-- For each end of each receipt in fct_rx_observation, the cells of the
-- positions lnk_02 read there, one row per distinct cell, and the cells its
-- own box covers (is_box_cell). The end is stationary when it sent at least
-- one position in the 7 days before the receipt and every two of them are the
-- same place; it then reads those positions, and its box covers their cells
-- that hold no finer one. Otherwise it reads the positions in the 7 days
-- before and the 30 minutes either side, and its box is the cell of the one
-- nearest in time within 30 minutes, the earlier on a tie (both windows
-- inclusive; Casey, issue #55, Q5, and PR #66, Q1 and Q2). any_precise says
-- whether a position read with that cell qualifies under pos_04. A receipt
-- whose relayer or gateway never sent a usable position is left out: lnk_02
-- drops it whatever the other end holds.

with positioned_nodes as (
    select distinct node_pseudonym
    from {{ ref('int_usable_position') }}
),

receipts as (
    select
        delivery_key as receipt_key,
        received_at,
        tx_node_hash,
        gateway_node_hash
    from {{ ref('fct_rx_observation') }}
    where
        tx_node_hash in (select node_pseudonym from positioned_nodes)
        and gateway_node_hash in (select node_pseudonym from positioned_nodes)
),

ends as (
    select receipt_key, received_at, 'relayer' as end_role, tx_node_hash as node_pseudonym
    from receipts
    union all
    select receipt_key, received_at, 'gateway' as end_role, gateway_node_hash as node_pseudonym
    from receipts
),

read_positions as (
    select
        ends.receipt_key,
        ends.end_role,
        ends.received_at as receipt_at,
        positions.*,
        positions.received_at <= ends.received_at as in_look_back,
        abs(extract(epoch from positions.received_at - ends.received_at)) as seconds_away,
        {{ holds_finer_between(
            'positions.finer_received_at',
            'ends.received_at - ' ~ stationary_window(),
            'ends.received_at'
        ) }} as set_aside
    from ends
    inner join {{ ref('int_usable_position') }} as positions
        on
            positions.node_pseudonym = ends.node_pseudonym
            and positions.received_at
            between ends.received_at - {{ stationary_window() }}
            and ends.received_at + {{ fallback_window() }}
),

stationarity as (
    select
        receipt_key,
        end_role,
        bool_or(in_look_back)
        and coalesce(
            min(next_conflict_at) filter (where in_look_back) > max(receipt_at), true
        ) as stationary
    from read_positions
    group by receipt_key, end_role
),

fallback_choice as (
    select distinct on (receipt_key, end_role)
        receipt_key,
        end_role,
        delivery_key
    from read_positions
    where seconds_away <= extract(epoch from {{ fallback_window() }})
    order by receipt_key, end_role, seconds_away, received_at, delivery_key
),

read_by_rule as (
    select
        read_positions.*,
        stationarity.stationary,
        case
            when stationarity.stationary then not read_positions.set_aside
            else read_positions.delivery_key = fallback_choice.delivery_key
        end as covered
    from read_positions
    inner join stationarity
        on
            stationarity.receipt_key = read_positions.receipt_key
            and stationarity.end_role = read_positions.end_role
    left join fallback_choice
        on
            fallback_choice.receipt_key = read_positions.receipt_key
            and fallback_choice.end_role = read_positions.end_role
    where read_positions.in_look_back or not stationarity.stationary
)

select
    receipt_key,
    end_role,
    stationary,
    south_i,
    north_i,
    west_i,
    east_i,
    precision_bits,
    min(latitude_i) as latitude_i,
    min(longitude_i) as longitude_i,
    min(half_width_ns_m) as half_width_ns_m,
    min(half_width_ew_m) as half_width_ew_m,
    bool_or(precise_source) as any_precise,
    coalesce(bool_or(covered), false) as is_box_cell
from read_by_rule
group by receipt_key, end_role, stationary, south_i, north_i, west_i, east_i, precision_bits
