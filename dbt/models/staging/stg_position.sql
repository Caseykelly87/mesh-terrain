{{ config(enabled=reads_raw()) }}

-- One row per raw Position or MapReport row, the cell kept as sent (pos_01).
-- Beyond renaming, staging applies pos_03 and pos_01 here, as the contract
-- asks: a Position at 0, 0 is no position and is flagged; a row needs both
-- coordinates to have a position; and only a row with a position carries
-- precision_bits, position_quality, cell_width_i and the half-widths. A
-- missing coordinate stays null and the other is kept as sent. Each
-- snapshot's copy of a delivery stays here; core keeps one per delivery.

with position_rows as (
    select
        snapshot_id,
        delivery_key,
        received_at,
        origin_pseudonym as node_pseudonym,
        packet_id,
        position_source,
        seen_public,
        seen_meshstl,
        latitude_i,
        longitude_i,
        precision_bits,
        coalesce(
            position_source = 'position' and latitude_i = 0 and longitude_i = 0,
            false
        ) as position_zero_mapped
    from {{ source('raw', 'mqtt_message') }}
    where position_source is not null
),

positioned as (
    select
        *,
        not position_zero_mapped
        and latitude_i is not null
        and longitude_i is not null as has_position
    from position_rows
),

cells as (
    select
        snapshot_id,
        delivery_key,
        received_at,
        node_pseudonym,
        packet_id,
        position_source,
        seen_public,
        seen_meshstl,
        position_zero_mapped,
        case when not position_zero_mapped then latitude_i end as latitude_i,
        case when not position_zero_mapped then longitude_i end as longitude_i,
        case when has_position then precision_bits end as precision_bits,
        case when has_position then {{ position_quality('precision_bits') }} end as position_quality,
        case when has_position then {{ cell_width_i('precision_bits') }} end as cell_width_i,
        case
            when has_position
                then {{ precise_flag('precision_bits', 'seen_meshstl', 'has_position') }}
        end as precise_source
    from positioned
)

select
    snapshot_id,
    delivery_key,
    received_at,
    node_pseudonym,
    packet_id,
    position_source,
    seen_public,
    seen_meshstl,
    position_zero_mapped,
    latitude_i,
    longitude_i,
    precision_bits,
    position_quality,
    cell_width_i,
    {{ half_width_ns_m('cell_width_i', 'latitude_i') }} as half_width_ns_m,
    {{ half_width_ew_m('cell_width_i', 'latitude_i') }} as half_width_ew_m,
    precise_source
from cells
