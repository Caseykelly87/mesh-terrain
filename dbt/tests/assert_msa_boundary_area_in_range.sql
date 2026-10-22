-- Census reports the St. Louis MSA at a little over 20,000 km2 of land and
-- water. A value far outside that range means the wrong feature or a bad CRS.
with area as (
    select
        geoid,
        st_area(st_transform(geom, 26915)) / 1000000.0 as area_km2
    from {{ ref('stg_msa_boundary') }}
)
select
    geoid,
    area_km2
from area
where area_km2 not between 18000 and 25000
