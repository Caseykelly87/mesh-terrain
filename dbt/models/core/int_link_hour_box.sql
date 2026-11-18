{{ config(enabled=reads_raw(), materialized='ephemeral') }}

-- Each end's box for the hour (lnk_02, one box per end for the hour; Casey,
-- issue #55, D1 and D2), chosen before lnk_05 from the link-hour's receipts
-- that have a position for both ends. An end gets one when it is stationary
-- at every one of them and every two of its positions from 7 days before the
-- first of them to the last of them are the same place; the box is then built
-- from that union, which can hold a position sent later in the hour than a
-- receipt, and every receipt of the hour uses it. One row per end that gets
-- an hour box; an end without one keeps each receipt's own box. Its precise
-- flag reads the union (PLAN, Data model; D7).

with receipt_ends as (
    select *
    from {{ ref('int_receipt_end') }}
),

boxed_receipts as (
    select receipt_key
    from receipt_ends
    group by receipt_key
    having bool_and(has_box)
),

hour_ends as (
    select
        receipt_ends.tx_node_hash,
        receipt_ends.gateway_node_hash,
        receipt_ends.hour,
        receipt_ends.end_role,
        receipt_ends.node_pseudonym,
        min(receipt_ends.received_at) as first_at,
        max(receipt_ends.received_at) as last_at
    from receipt_ends
    inner join boxed_receipts
        on boxed_receipts.receipt_key = receipt_ends.receipt_key
    group by 1, 2, 3, 4, 5
    having bool_and(receipt_ends.stationary)
),

union_positions as (
    select
        hour_ends.tx_node_hash,
        hour_ends.gateway_node_hash,
        hour_ends.hour,
        hour_ends.end_role,
        hour_ends.last_at,
        positions.*,
        {{ holds_finer_between(
            'positions.finer_received_at',
            'hour_ends.first_at - ' ~ stationary_window(),
            'hour_ends.last_at'
        ) }} as set_aside
    from hour_ends
    inner join {{ ref('int_usable_position') }} as positions
        on
            positions.node_pseudonym = hour_ends.node_pseudonym
            and positions.received_at
            between hour_ends.first_at - {{ stationary_window() }} and hour_ends.last_at
),

stationary_ends as (
    select
        tx_node_hash,
        gateway_node_hash,
        hour,
        end_role
    from union_positions
    group by 1, 2, 3, 4
    having coalesce(min(next_conflict_at) > max(last_at), true)
),

union_cells as (
    select
        union_positions.tx_node_hash,
        union_positions.gateway_node_hash,
        union_positions.hour,
        union_positions.end_role,
        union_positions.south_i,
        union_positions.north_i,
        union_positions.west_i,
        union_positions.east_i,
        union_positions.precision_bits,
        min(union_positions.half_width_ns_m) as half_width_ns_m,
        min(union_positions.half_width_ew_m) as half_width_ew_m,
        bool_or(union_positions.precise_source) as any_precise,
        bool_or(not union_positions.set_aside) as covered
    from union_positions
    inner join stationary_ends
        on
            stationary_ends.tx_node_hash = union_positions.tx_node_hash
            and stationary_ends.gateway_node_hash = union_positions.gateway_node_hash
            and stationary_ends.hour = union_positions.hour
            and stationary_ends.end_role = union_positions.end_role
    group by 1, 2, 3, 4, 5, 6, 7, 8, 9
),

covered as (
    select *
    from union_cells
    where covered
)

{{ box_from_cells('covered', ['tx_node_hash', 'gateway_node_hash', 'hour', 'end_role']) }}
