{{ config(enabled=reads_raw()) }}

-- pos_02: only positions inside the MSA polygon enter core. A position is its
-- stored value (latitude_i, longitude_i in 1e-7 degrees), which at 32 bits is
-- the raw fix and not its cell centre (Casey, PR #66, Q3). st_within excludes
-- a point on the boundary itself. Returns each core row outside.

with boundary as (
    select geom
    from {{ ref('stg_msa_boundary') }}
)

select
    positions.delivery_key,
    positions.snapshot_id
from {{ ref('fct_node_position') }} as positions
cross join boundary
where not st_within(
    st_setsrid(
        st_point(
            (positions.longitude_i * 1e-7)::double precision,
            (positions.latitude_i * 1e-7)::double precision
        ),
        4326
    ),
    boundary.geom
)
