{{ config(enabled=reads_raw()) }}

-- One row per neighbour listed in a NeighborInfo report, unnested from raw's
-- four arrays in message order. Beyond renaming, a neighbour low byte of 0
-- reads as 255, as in stg_packet, since firmware sends a relayer whose low
-- byte is 0 as relay_node 0xFF (agreed with ingest-eng, PR #15). Each
-- snapshot's copy of a report stays here; lnk_01 counts distinct neighbours,
-- so copies change nothing.

with reports as (
    select
        snapshot_id,
        delivery_key,
        received_at,
        origin_pseudonym,
        gateway_pseudonym,
        neighbour_pseudonyms,
        neighbour_low_bytes,
        neighbour_snrs,
        neighbour_last_heard
    from {{ source('raw', 'mqtt_message') }}
    where neighbour_pseudonyms is not null
),

neighbours as (
    select
        reports.snapshot_id,
        reports.delivery_key,
        reports.received_at,
        reports.origin_pseudonym,
        reports.gateway_pseudonym,
        listed.neighbour_pseudonym,
        listed.neighbour_low_byte,
        listed.neighbour_snr,
        listed.neighbour_last_heard,
        listed.neighbour_position
    from reports
    cross join lateral unnest(
        reports.neighbour_pseudonyms,
        reports.neighbour_low_bytes,
        reports.neighbour_snrs,
        reports.neighbour_last_heard
    ) with ordinality as listed (
        neighbour_pseudonym,
        neighbour_low_byte,
        neighbour_snr,
        neighbour_last_heard,
        neighbour_position
    )
)

select
    snapshot_id,
    delivery_key,
    neighbour_position::integer as neighbour_position,
    received_at,
    origin_pseudonym as reporter_pseudonym,
    gateway_pseudonym,
    neighbour_pseudonym,
    case when neighbour_low_byte = 0 then 255 else neighbour_low_byte end::smallint
        as neighbour_low_byte,
    neighbour_snr,
    neighbour_last_heard
from neighbours
