{{ config(enabled=reads_raw()) }}

-- No packet_id in staging is 0: raw lands a packet_id of 0 as null (ingest-eng's
-- interface, docs/STATUS.md), and the link rules treat only a missing id as no
-- id (Casey, PR #72, his answer to PR #79, Q2: "Also add a staging test that
-- fails if packet_id is ever 0, so the decoder assumption is tested rather
-- than trusted"). Runs on every build that reads raw, real builds included.
-- Returns each row holding 0.

select
    delivery_key,
    'stg_packet' as model
from {{ ref('stg_packet') }}
where packet_id = 0

union all

select
    delivery_key,
    'stg_position' as model
from {{ ref('stg_position') }}
where packet_id = 0
