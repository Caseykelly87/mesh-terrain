{{ config(enabled=reads_raw(), materialized='ephemeral') }}

-- The positions lnk_02 reads: a node's usable positions inside the MSA, from
-- either broker and either source (fct_node_position holds only positions
-- inside the MSA). One broadcast heard by several gateways is several
-- deliveries in core; here it is one position, at the received_at of its
-- first copy, as ADR 0005 times a delivery by its first sighting, and
-- precise when any copy qualifies under pos_04. A position without a
-- packet_id stays one row per delivery.
--
-- Each carries its cell's half-open edges and next_conflict_at, the earliest
-- received_at at or after its own of another position of the node that is
-- not the same place: the cells do not overlap and the stored values lie more
-- than STATIONARY_FLOOR_M apart. A set of positions received from t0 to t1 is
-- pairwise the same place exactly when none of them has next_conflict_at at
-- or before t1, so the pairwise test, never chained, is read once per window
-- instead of once per pair. Likewise finer_received_at lists when the node
-- sent each finer position this cell holds, so a box built over any window
-- can set the cell aside (lnk_02, D3') without comparing the window's cells
-- with each other.

with deliveries as (
    select
        core_positions.delivery_key,
        core_positions.node_pseudonym,
        coalesce(staged.packet_id::text, core_positions.delivery_key) as broadcast_key,
        core_positions.received_at,
        core_positions.latitude_i,
        core_positions.longitude_i,
        core_positions.precision_bits,
        core_positions.cell_width_i,
        core_positions.half_width_ns_m,
        core_positions.half_width_ew_m,
        core_positions.precise_source
    from {{ ref('fct_node_position') }} as core_positions
    left join {{ ref('stg_position') }} as staged
        on
            staged.snapshot_id = core_positions.snapshot_id
            and staged.delivery_key = core_positions.delivery_key
    where core_positions.position_quality = 'usable'
),

broadcasts as (
    select
        min(delivery_key) as delivery_key,
        node_pseudonym,
        min(received_at) as received_at,
        latitude_i,
        longitude_i,
        precision_bits,
        cell_width_i,
        min(half_width_ns_m) as half_width_ns_m,
        min(half_width_ew_m) as half_width_ew_m,
        bool_or(precise_source) as precise_source
    from deliveries
    group by node_pseudonym, broadcast_key, latitude_i, longitude_i, precision_bits, cell_width_i
),

positions as (
    select
        *,
        {{ cell_low_i('latitude_i', 'precision_bits', 'cell_width_i') }} as south_i,
        {{ cell_low_i('latitude_i', 'precision_bits', 'cell_width_i') }} + cell_width_i as north_i,
        {{ cell_low_i('longitude_i', 'precision_bits', 'cell_width_i') }} as west_i,
        {{ cell_low_i('longitude_i', 'precision_bits', 'cell_width_i') }} + cell_width_i as east_i,
        {{ stored_point('latitude_i', 'longitude_i') }}::geography as stored_point
    from broadcasts
),

conflicts as (
    select
        earlier.delivery_key,
        min(later.received_at) as next_conflict_at
    from positions as earlier
    inner join positions as later
        on
            later.node_pseudonym = earlier.node_pseudonym
            and later.received_at >= earlier.received_at
            and later.delivery_key <> earlier.delivery_key
    where
        not {{ boxes_overlap('earlier.', 'later.') }}
        and st_distance(earlier.stored_point, later.stored_point) > {{ stationary_floor_m() }}
    group by earlier.delivery_key
),

finer_held as (
    select
        coarse.delivery_key,
        array_agg(finer.received_at order by finer.received_at) as finer_received_at
    from positions as coarse
    inner join positions as finer
        on
            finer.node_pseudonym = coarse.node_pseudonym
            and finer.precision_bits > coarse.precision_bits
    where {{ box_holds('coarse.', 'finer.latitude_i', 'finer.longitude_i') }}
    group by coarse.delivery_key
)

select
    positions.delivery_key,
    positions.node_pseudonym,
    positions.received_at,
    positions.latitude_i,
    positions.longitude_i,
    positions.precision_bits,
    positions.south_i,
    positions.north_i,
    positions.west_i,
    positions.east_i,
    positions.half_width_ns_m,
    positions.half_width_ew_m,
    positions.precise_source,
    conflicts.next_conflict_at,
    coalesce(finer_held.finer_received_at, '{}') as finer_received_at
from positions
left join conflicts
    on conflicts.delivery_key = positions.delivery_key
left join finer_held
    on finer_held.delivery_key = positions.delivery_key
