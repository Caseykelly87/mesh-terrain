{{
    config(
        enabled=reads_raw(),
        materialized='incremental',
        unique_key='delivery_key',
        incremental_strategy='delete+insert'
    )
}}

-- One row per position delivery inside the MSA (pos_02). A position is its
-- stored value, which at 32 bits is the raw fix, not its cell centre (Casey,
-- PR #66, Q3). When several snapshots hold the delivery, the row's values come
-- from its first copy by received_at, then snapshot id, and seen_public and
-- seen_meshstl are true when any copy carried them, so precise_source reads
-- seen_meshstl on any copy of the delivery (pos_04; the any-copy reading
-- awaits Casey on PR #69).
--
-- Incremental: a run reads the positions at or after the latest received_at
-- already here, and every position of a snapshot not yet here, since a new
-- snapshot can cover earlier hours. Each delivery it touches is rebuilt from
-- its new copies and the row already here, so a later copy can add a broker
-- flag and an earlier copy replaces the row; an incremental run ends where a
-- full refresh would. Rows added to a snapshot already built, which only the
-- synthetic fixtures can do, need --full-refresh.

with new_positions as (
    select *
    from {{ ref('stg_position') }}
    where
        latitude_i is not null
        and longitude_i is not null
    {% if is_incremental() %}
        and (
            received_at >= (select max(received_at) from {{ this }})
            or snapshot_id not in (select distinct snapshot_id from {{ this }})
        )
    {% endif %}
),

boundary as (
    select geom
    from {{ ref('stg_msa_boundary') }}
),

new_copies as (
    select
        new_positions.delivery_key,
        new_positions.snapshot_id,
        new_positions.received_at,
        new_positions.node_pseudonym,
        new_positions.position_source,
        new_positions.latitude_i,
        new_positions.longitude_i,
        new_positions.precision_bits,
        new_positions.position_quality,
        new_positions.cell_width_i,
        new_positions.half_width_ns_m,
        new_positions.half_width_ew_m,
        new_positions.seen_public,
        new_positions.seen_meshstl
    from new_positions
    cross join boundary
    where st_within(
        st_setsrid(
            st_point(
                (new_positions.longitude_i * 1e-7)::double precision,
                (new_positions.latitude_i * 1e-7)::double precision
            ),
            4326
        ),
        boundary.geom
    )
),

all_copies as (
    select *
    from new_copies
    {% if is_incremental() %}
        union all
        select
            delivery_key,
            snapshot_id,
            received_at,
            node_pseudonym,
            position_source,
            latitude_i,
            longitude_i,
            precision_bits,
            position_quality,
            cell_width_i,
            half_width_ns_m,
            half_width_ew_m,
            seen_public,
            seen_meshstl
        from {{ this }}
        where delivery_key in (select delivery_key from new_copies)
    {% endif %}
),

broker_flags as (
    select
        delivery_key,
        bool_or(seen_public) as seen_public,
        bool_or(seen_meshstl) as seen_meshstl
    from all_copies
    group by delivery_key
),

ranked_copies as (
    select
        *,
        row_number() over (
            partition by delivery_key
            order by received_at, snapshot_id
        ) as copy_rank
    from all_copies
),

first_copies as (
    select *
    from ranked_copies
    where copy_rank = 1
)

select
    first_copies.delivery_key,
    first_copies.snapshot_id,
    first_copies.received_at,
    first_copies.node_pseudonym,
    first_copies.position_source,
    first_copies.latitude_i,
    first_copies.longitude_i,
    first_copies.precision_bits,
    first_copies.position_quality,
    first_copies.cell_width_i,
    first_copies.half_width_ns_m,
    first_copies.half_width_ew_m,
    broker_flags.seen_public,
    broker_flags.seen_meshstl,
    coalesce(first_copies.precision_bits >= 20 and broker_flags.seen_meshstl, false)
        as precise_source
from first_copies
inner join broker_flags
    on broker_flags.delivery_key = first_copies.delivery_key
