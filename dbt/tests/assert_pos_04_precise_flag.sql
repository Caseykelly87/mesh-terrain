{{ config(enabled=reads_raw()) }}

-- pos_04: a position is precise_source exactly when its delivery has
-- seen_meshstl true on any copy (Casey, issue #55 comment 6985802330, item 7) and its
-- precision_bits is 20 or more, or it is a Position at 0 bits (item 8), so a
-- public-broker position never carries the flag (Casey, issue #55, D10); a
-- position without precision_bits is not flagged. Written out here rather
-- than through the model's macro. Returns each core row whose flag is
-- missing or differs.

select
    delivery_key,
    position_source,
    precision_bits,
    seen_meshstl,
    precise_source
from {{ ref('fct_node_position') }}
where
    precise_source is null
    or precise_source <> coalesce(
        seen_meshstl
        and (precision_bits >= 20 or (position_source = 'position' and precision_bits = 0)),
        false
    )
