{{ config(enabled=reads_raw()) }}

-- pos_04: a position is precise_source exactly when precision_bits is 20 or
-- more and its delivery has seen_meshstl true, so a public-broker position
-- never carries the flag (Casey, issue #55, D10); a position without
-- precision_bits is not flagged. Returns each core row whose flag is missing
-- or differs.

select
    delivery_key,
    precision_bits,
    seen_meshstl,
    precise_source
from {{ ref('fct_node_position') }}
where
    precise_source is null
    or precise_source <> coalesce(precision_bits >= 20 and seen_meshstl, false)
