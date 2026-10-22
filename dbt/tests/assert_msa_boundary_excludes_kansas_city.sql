-- Downtown Kansas City is about 370 km west; containing it would mean the
-- polygon is a state or national outline rather than the MSA.
select geoid
from {{ ref('stg_msa_boundary') }}
where st_contains(geom, st_setsrid(st_point(-94.5786, 39.0997), 4326))
