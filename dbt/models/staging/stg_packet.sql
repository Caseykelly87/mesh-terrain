{{ config(enabled=reads_raw()) }}

-- One row per packet heard at a gateway (ing_06). Beyond renaming, staging
-- does two things the contract asks of it here: it keeps the first copy by
-- received_at of a packet (origin, packet_id) at one gateway, within a
-- snapshot and across snapshots; and it reads a low byte of 0 as 255, since
-- firmware sends such a relayer as relay_node 0xFF (agreed with ingest-eng,
-- PR #15). A row without packet_id or gateway cannot be matched to another
-- copy of its packet, so only another copy of the same delivery, in another
-- snapshot, collapses into it. Ties on received_at go to the snapshot and
-- then the delivery key that sort first, so a rebuild keeps the same row.
-- A reading the radio chips cannot report lands as missing (lnk_06:
-- impossible values are treated as missing): an SNR that is not a number or
-- outside -32 to 31.75 dB, an RSSI outside -196 to +115 dBm, both inclusive
-- (Casey, PR #72; the datasheet sections are cited in stg_packet.yml).
-- Plausibility stays lnk_06's job, in fct_rx_observation. Coordinates stay out: positions are
-- stg_position's, and nothing leaves staging at 0, 0 (pos_03). hop_start and
-- hop_limit stay out: no link rule reads either (lnk_01).

with deliveries as (
    select
        snapshot_id,
        delivery_key,
        received_at,
        topic,
        modem_preset,
        seen_public,
        seen_meshstl,
        decode_status,
        origin_pseudonym,
        origin_low_byte,
        gateway_pseudonym,
        gateway_low_byte,
        packet_id,
        relay_node,
        via_mqtt,
        rx_time,
        rx_snr,
        rx_rssi,
        channel,
        portnum,
        payload_length,
        decoder_version
    from {{ source('raw', 'mqtt_message') }}
),

ranked_copies as (
    select
        *,
        row_number() over (
            partition by
                origin_pseudonym,
                packet_id,
                gateway_pseudonym,
                case when packet_id is null or gateway_pseudonym is null then delivery_key end
            order by received_at, snapshot_id, delivery_key
        ) as copy_rank
    from deliveries
)

select
    delivery_key,
    snapshot_id,
    received_at,
    topic,
    modem_preset,
    seen_public,
    seen_meshstl,
    decode_status,
    origin_pseudonym,
    case when origin_low_byte = 0 then 255 else origin_low_byte end::smallint as origin_low_byte,
    gateway_pseudonym,
    case when gateway_low_byte = 0 then 255 else gateway_low_byte end::smallint as gateway_low_byte,
    packet_id,
    relay_node,
    via_mqtt,
    rx_time,
    case when rx_snr between -32 and 31.75 then rx_snr end as rx_snr,
    case when rx_rssi between -196 and 115 then rx_rssi end as rx_rssi,
    channel,
    portnum,
    payload_length,
    decoder_version
from ranked_copies
where copy_rank = 1
