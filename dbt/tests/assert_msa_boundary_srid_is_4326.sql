-- Every geometry column in the warehouse is stored in SRID 4326.
-- The view's geometry(MultiPolygon, 4326) cast already enforces this; the
-- test guards against the cast being removed.
select geoid
from {{ ref('stg_msa_boundary') }}
where st_srid(geom) <> 4326
