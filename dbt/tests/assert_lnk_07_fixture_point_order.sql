{{ config(enabled=fixture_build()) }}

-- lnk_07's point order on the link-rules fixture: each kept hour's first
-- point is the centre of its hand-worked relayer box and its second the
-- centre of its gateway box, x the longitude and y the latitude, within
-- 1e-9 degrees (Casey, PR #66 comment 6968763406, item 4). Returns each kept
-- hour whose geometry is missing or whose points differ.

with expected as (
    select
        relayer,
        gateway,
        hour,
        (relayer_west_i + relayer_east_i) * 0.5e-7 as relayer_x,
        (relayer_south_i + relayer_north_i) * 0.5e-7 as relayer_y,
        (gateway_west_i + gateway_east_i) * 0.5e-7 as gateway_x,
        (gateway_south_i + gateway_north_i) * 0.5e-7 as gateway_y
    from {{ ref('expected_link_hours') }}
    where outcome = 'kept'
)

select
    expected.relayer,
    expected.gateway,
    expected.hour
from expected
left join {{ ref('int_link_window') }} as windows
    on
        windows.tx_node_hash = expected.relayer
        and windows.gateway_node_hash = expected.gateway
        and windows.hour = expected.hour
where
    windows.geom is null
    or abs(st_x(st_pointn(windows.geom, 1)) - expected.relayer_x) > 1e-9
    or abs(st_y(st_pointn(windows.geom, 1)) - expected.relayer_y) > 1e-9
    or abs(st_x(st_pointn(windows.geom, 2)) - expected.gateway_x) > 1e-9
    or abs(st_y(st_pointn(windows.geom, 2)) - expected.gateway_y) > 1e-9
