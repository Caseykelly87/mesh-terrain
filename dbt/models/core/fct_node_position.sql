{{
    config(
        enabled=reads_raw(),
        materialized='incremental',
        unique_key='delivery_key',
        incremental_strategy='delete+insert'
    )
}}

-- One row per position delivery inside the MSA (pos_02), from the first copy
-- by received_at, then snapshot id, when several snapshots hold it. A
-- position is its stored value, which at 32 bits is the raw fix, not its
-- cell centre (Casey, PR #66, Q3).
--
-- Incremental: a run reads the positions at or after the latest received_at
-- already here, and every position of a snapshot not yet here, since a new
-- snapshot can cover earlier hours. A copy replaces the row of its delivery
-- only when it comes first in that order, so an incremental run ends where a
-- full refresh would. Rows added to a snapshot already built, which only the
-- synthetic fixtures can do, need --full-refresh.

with positions as (
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

inside_msa as (
    select positions.*
    from positions
    cross join boundary
    where st_within(
        st_setsrid(
            st_point(
                (positions.longitude_i * 1e-7)::double precision,
                (positions.latitude_i * 1e-7)::double precision
            ),
            4326
        ),
        boundary.geom
    )
),

ranked_copies as (
    select
        *,
        row_number() over (
            partition by delivery_key
            order by received_at, snapshot_id
        ) as copy_rank
    from inside_msa
),

first_copies as (
    select *
    from ranked_copies
    where copy_rank = 1
)

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
    seen_meshstl,
    precise_source
from first_copies
{% if is_incremental() %}
    where not exists (
        select 1
        from {{ this }} as kept
        where
            kept.delivery_key = first_copies.delivery_key
            and (kept.received_at, kept.snapshot_id)
            <= (first_copies.received_at, first_copies.snapshot_id)
    )
{% endif %}
