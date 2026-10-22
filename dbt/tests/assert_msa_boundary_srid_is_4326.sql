-- Every geometry column in the warehouse is stored in SRID 4326.
select geoid
from {{ ref('stg_msa_boundary') }}
where st_srid(geom) <> 4326
