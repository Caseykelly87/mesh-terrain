-- The boundary polygon must pass st_isvalid; a self-intersecting ring would
-- make every st_contains clip downstream unreliable.
select geoid
from {{ ref('stg_msa_boundary') }}
where not st_isvalid(geom)
