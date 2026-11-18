{{ config(enabled=reads_raw()) }}

-- No low-byte column in staging holds 0: an origin, gateway or neighbour low
-- byte of 0 reads as 255, since firmware sends a relayer whose low byte is 0
-- as relay_node 0xFF, and raw lands a relay_node of 0 as null (Casey, issue
-- #55, his message on PR #69, item 1 (b)). Returns each row holding 0.

select
    delivery_key,
    'stg_packet' as model
from {{ ref('stg_packet') }}
where origin_low_byte = 0 or gateway_low_byte = 0 or relay_node = 0

union all

select
    delivery_key,
    'stg_neighbor' as model
from {{ ref('stg_neighbor') }}
where neighbour_low_byte = 0
