-- Downtown Kansas City is about 385 km west of the Arch; containing it would
-- mean the polygon is a state or national outline rather than the MSA.
select geoid
from {{ ref('stg_msa_boundary') }}
where st_contains(geom, st_setsrid(st_point(-94.5786, 39.0997), 4326))
