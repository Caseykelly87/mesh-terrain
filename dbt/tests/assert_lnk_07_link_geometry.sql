{{ config(enabled=reads_raw()) }}

-- lnk_07 on every link-hour: the geometry is valid, a two-point LINESTRING
-- in SRID 4326, its first point the centre of the relayer box and its second
-- the centre of the gateway box, each the midpoint of the row's own edges
-- (Casey, PR #66 comment 6968763406, item 4). Returns each row that breaks it.

select
    tx_node_hash,
    gateway_node_hash,
    hour
from {{ ref('int_link_window') }}
where
    geom is null
    or not st_isvalid(geom)
    or geometrytype(geom) <> 'LINESTRING'
    or st_srid(geom) <> 4326
    or st_numpoints(geom) <> 2
    or abs(st_x(st_pointn(geom, 1)) - (relayer_west_i + relayer_east_i) * 0.5e-7) > 1e-9
    or abs(st_y(st_pointn(geom, 1)) - (relayer_south_i + relayer_north_i) * 0.5e-7) > 1e-9
    or abs(st_x(st_pointn(geom, 2)) - (gateway_west_i + gateway_east_i) * 0.5e-7) > 1e-9
    or abs(st_y(st_pointn(geom, 2)) - (gateway_south_i + gateway_north_i) * 0.5e-7) > 1e-9
