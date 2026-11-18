{{ config(enabled=fixture_build()) }}

-- bearing_deg is the grid bearing in EPSG:26915 from the relayer box centre to
-- the gateway box centre, clockwise from grid north, as the engine's
-- bearing_deg (docs/INTERFACE.md). c01's kept hour runs from r20's centre to
-- g16's centre; the expected 262.186148 degrees comes from pyproj, the two
-- centres transformed from EPSG:4326 to EPSG:26915 and
-- degrees(atan2(east, north)) mod 360, as engine/predict.py computes it. The
-- geodesic azimuth, 263.830264 degrees, differs by the grid convergence.
-- Returns the row when it is missing or differs by more than 1e-6 degrees.

select
    'c01-r c01-g 10:00' as item,
    windows.bearing_deg
from (select 1) as anchor
left join {{ ref('int_link_window') }} as windows
    on
        windows.tx_node_hash = 'c01-r'
        and windows.gateway_node_hash = 'c01-g'
        and windows.hour = '2026-10-23 10:00:00+00'::timestamptz
where coalesce(abs(windows.bearing_deg - 262.186148034), 1) > 1e-6
