-- The Gateway Arch is in the middle of the metro; if it falls outside, the
-- wrong feature was extracted or the axis order is swapped. coalesce makes a
-- null geometry fail here too, since st_contains on null returns null.
select geoid
from {{ ref('stg_msa_boundary') }}
where not coalesce(st_contains(geom, st_setsrid(st_point(-90.1848, 38.6247), 4326)), false)
