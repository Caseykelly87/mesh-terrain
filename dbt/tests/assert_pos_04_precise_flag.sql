{{ config(enabled=reads_raw()) }}

-- pos_04, written out here rather than through the models' macro. A position
-- is precise_source exactly when it carries coordinates, its delivery has
-- seen_meshstl true, and its precision_bits is 20 or more, or 0, Position or
-- MapReport alike. The contract's pos_04 (PR #71), quoted whole, one quote per
-- line. From Casey, issue #55 comment 6989099568, item 2:
-- "or a position with `precision_bits` 0 that carries coordinates, Position or MapReport, is flagged `precise_source = true` exactly when `seen_meshstl` is true on its delivery"
-- From Casey, PR #71 comment 6989275630, item 1:
-- "a position carries coordinates when `latitude_i` and `longitude_i` are both present and `pos_03` does not map it to no position, so a report at 0, 0 is not flagged"
-- A public-broker position never carries the flag (Casey, issue #55, D10).
--
-- stg_position reads this copy's seen_meshstl and leaves the flag null on a
-- row with no position, a Position at 0, 0 included. Core reads seen_meshstl
-- on any copy of the delivery (Casey, issue #55 comment 6985802330, item 7);
-- a row reaches core only with both coordinates. Returns each row whose flag
-- differs.

with staged as (
    select
        'stg_position' as model_name,
        snapshot_id,
        delivery_key,
        precise_source,
        case
            when
                latitude_i is not null
                and longitude_i is not null
                and not position_zero_mapped
                then coalesce(
                    seen_meshstl and (precision_bits >= 20 or precision_bits = 0),
                    false
                )
        end as expected_flag
    from {{ ref('stg_position') }}
),

core_rows as (
    select
        'fct_node_position' as model_name,
        snapshot_id,
        delivery_key,
        precise_source,
        coalesce(
            latitude_i is not null
            and longitude_i is not null
            and seen_meshstl
            and (precision_bits >= 20 or precision_bits = 0),
            false
        ) as expected_flag
    from {{ ref('fct_node_position') }}
),

all_rows as (
    select *
    from staged
    union all
    select *
    from core_rows
)

select *
from all_rows
where precise_source is distinct from expected_flag
