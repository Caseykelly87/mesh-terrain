{{ config(enabled=reads_raw()) }}

-- pos_03: no row leaving staging has a position at 0, 0. stg_position maps a
-- Position at 0, 0 to no position, and stg_packet carries no coordinates, so
-- only stg_position is read. Returns each row still at 0, 0.

select
    snapshot_id,
    delivery_key,
    position_source
from {{ ref('stg_position') }}
where
    latitude_i = 0
    and longitude_i = 0
