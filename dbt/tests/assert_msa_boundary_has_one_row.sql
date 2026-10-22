-- The clip is one polygon. Zero rows would clip everything away and two
-- would double every point that falls in both.
with boundary_count as (
    select count(*) as boundary_rows
    from {{ ref('stg_msa_boundary') }}
)
select boundary_rows
from boundary_count
where boundary_rows <> 1
