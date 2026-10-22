-- The clip is one polygon. Zero rows would clip everything away and two
-- would double every point that falls in both.
with row_count as (
    select count(*) as n
    from {{ ref('stg_msa_boundary') }}
)
select n
from row_count
where n <> 1
