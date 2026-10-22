-- The seed carries the boundary as WKT because dbt seeds are CSV, split into
-- chunks because a CSV field is capped at 131072 characters when dbt reads
-- it. This view joins the chunks and parses them once so no later model
-- handles text. There is no repair step: an invalid future vintage must fail
-- assert_msa_boundary_is_valid rather than be silently fixed here.

with wkt_chunks as (
    select
        geoid,
        name,
        part,
        geometry_wkt
    from {{ ref('msa_boundary') }}
),

wkt_text as (
    select
        geoid,
        name,
        string_agg(geometry_wkt, '' order by part) as geometry_wkt
    from wkt_chunks
    group by geoid, name
)

select
    geoid,
    name,
    st_multi(st_geomfromtext(geometry_wkt, 4326))::geometry(multipolygon, 4326) as geom
from wkt_text
