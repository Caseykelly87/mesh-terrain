{{
    config(
        enabled=reads_raw(),
        materialized='incremental',
        unique_key=['tx_node_hash', 'gateway_node_hash', 'hour'],
        incremental_strategy='delete+insert'
    )
}}

-- One row per kept link-hour, (tx_node_hash, gateway_node_hash, hour)
-- (lnk_08): the last relayer, the gateway and the clock hour, kept by
-- lnk_04's order (int_link_hour). Each end keeps the box it used under
-- lnk_02. geom runs from the relayer box centre to the gateway box centre in
-- that order (lnk_07; Casey, PR #66 comment 6968763406, item 4), and
-- bearing_deg is the geodesic azimuth along it.
--
-- Incremental: a run adds the link-hours after the latest hour already here,
-- by last_received_at. Data that changes that hour or an earlier one needs
-- --full-refresh.

{% set box_columns = ['south_i', 'north_i', 'west_i', 'east_i', 'merged', 'precision_bits', 'half_width_ns_m', 'half_width_ew_m', 'precise_source'] %}

with kept_hours as (
    select *
    from {{ ref('int_link_hour') }}
    where drop_rule is null
    {% if is_incremental() %}
        and hour > (select max(hour) from {{ this }})
    {% endif %}
)

select
    tx_node_hash,
    gateway_node_hash,
    hour,
    packet_count,
    first_received_at,
    last_received_at,
    median_snr_db,
    median_rssi_dbm,
    modem_preset,
    distance_m,
    degrees(
        st_azimuth(
            {{ box_centre('relayer_') }}::geography,
            {{ box_centre('gateway_') }}::geography
        )
    ) as bearing_deg,
    position_uncertainty_m,
    {% for column in box_columns -%}
    relayer_{{ column }},
    {% endfor -%}
    {% for column in box_columns -%}
    gateway_{{ column }},
    {% endfor -%}
    st_makeline({{ box_centre('relayer_') }}, {{ box_centre('gateway_') }})::geometry(linestring, 4326)
        as geom
from kept_hours
