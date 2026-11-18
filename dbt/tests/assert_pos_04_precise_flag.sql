{{ config(enabled=reads_raw()) }}

-- pos_04: a position is precise_source exactly when its delivery has
-- seen_meshstl true on any copy (Casey, issue #55 comment 6985802330, item
-- 7) and its precision_bits is 20 or more, or 0, Position or MapReport alike
-- (Casey, issue #55 comment 6989099568, item 2; PR #71: "or a position with precision_bits 0 that carries coordinates, Position or MapReport, is flagged precise_source = true exactly when seen_meshstl is true on its delivery").
-- So a public-broker position never carries the flag (Casey, issue #55, D10),
-- and a position without precision_bits is not flagged. Written out here
-- rather than through the model's macro. Returns each core row whose flag is
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
        and (precision_bits >= 20 or precision_bits = 0),
        false
    )
