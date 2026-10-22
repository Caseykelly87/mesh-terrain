-- The seed carries the boundary as WKT because dbt seeds are CSV, split into
-- chunks because a CSV field is capped at 131072 characters when dbt reads
-- it. This view joins the chunks and parses them once so no later model
-- handles text. st_makevalid runs only when the parsed geometry is invalid;
-- the 2025 TIGER feature parses valid, so that branch guards a future vintage.

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
),

parsed as (
    select
        geoid,
        name,
        st_geomfromtext(geometry_wkt, 4326) as raw_geom
    from wkt_text
),

repaired as (
    select
        geoid,
        name,
        case
            when st_isvalid(raw_geom) then raw_geom
            else st_collectionextract(st_makevalid(raw_geom), 3)
        end as valid_geom
    from parsed
)

select
    geoid,
    name,
    st_multi(valid_geom)::geometry(multipolygon, 4326) as geom
from repaired
