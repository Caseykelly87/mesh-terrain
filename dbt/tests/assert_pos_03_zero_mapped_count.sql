{{ config(enabled=reads_raw()) }}

-- pos_03: the count of stg_position rows flagged position_zero_mapped equals
-- the raw rows with position_source 'position' and both coordinates 0.
-- Returns one row when the two counts differ.

with zero_in_source as (
    select count(*) as row_count
    from {{ source('raw', 'mqtt_message') }}
    where
        position_source = 'position'
        and latitude_i = 0
        and longitude_i = 0
),

staged_zero as (
    select count(*) as row_count
    from {{ ref('stg_position') }}
    where position_zero_mapped
)

select
    zero_in_source.row_count as source_rows,
    staged_zero.row_count as mapped_rows
from zero_in_source
cross join staged_zero
where zero_in_source.row_count <> staged_zero.row_count
