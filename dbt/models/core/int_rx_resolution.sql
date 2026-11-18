{{ config(enabled=reads_raw(), materialized='ephemeral') }}

-- Every packet a gateway uplinked, with the per-receipt link rules applied in
-- the contract's order: lnk_01 (a radio receipt with a relay_node, its last
-- relayer resolved to one known node), lnk_03 (relayer and gateway differ)
-- and lnk_06 (both readings in range). drop_rule names the first rule a row
-- fails; fct_rx_observation keeps the rows that fail none.
--
-- lnk_01: relay_node equal to the sender's own low byte is a direct hop.
-- Otherwise the candidates are the senders of every radio delivery the
-- gateway heard in the 24 hours before the receipt, both edges included, its
-- own packets and the receipt itself included. NeighborInfo resolves the byte
-- when the gateway's own reports in the same 24 hours list exactly one
-- neighbour with it, counted across all of them. Two methods that resolve to
-- different nodes drop the receipt, a direct hop included. hop_start and
-- hop_limit are never read.

with packets as (
    select *
    from {{ ref('stg_packet') }}
    where gateway_pseudonym is not null
),

receipts as (
    select *
    from packets
    where
        not via_mqtt
        and origin_pseudonym <> gateway_pseudonym
        and relay_node is not null
),

radio_deliveries as (
    select
        gateway_pseudonym,
        origin_pseudonym,
        origin_low_byte,
        received_at,
        received_at - lag(received_at) over (
            partition by gateway_pseudonym, origin_pseudonym, origin_low_byte
            order by received_at
        ) > {{ candidate_window() }} as starts_span
    from packets
    where not via_mqtt
),

-- A span is a run of one sender's radio deliveries at one gateway with no
-- gap over 24 hours. The sender was heard in the 24 hours up to t, both
-- edges included, exactly when some span has first_heard_at <= t <=
-- last_heard_at + 24 hours, so each receipt meets a few spans instead of
-- every delivery.
heard_spans as (
    select
        gateway_pseudonym,
        origin_pseudonym,
        origin_low_byte,
        min(received_at) as first_heard_at,
        max(received_at) as last_heard_at
    from (
        select
            *,
            count(*) filter (where starts_span) over (
                partition by gateway_pseudonym, origin_pseudonym, origin_low_byte
                order by received_at
            ) as span_number
        from radio_deliveries
    ) as numbered
    group by gateway_pseudonym, origin_pseudonym, origin_low_byte, span_number
),

candidate_matches as (
    select
        receipts.delivery_key,
        count(distinct heard_spans.origin_pseudonym) as candidate_count,
        min(heard_spans.origin_pseudonym) as candidate
    from receipts
    inner join heard_spans
        on
            heard_spans.gateway_pseudonym = receipts.gateway_pseudonym
            and heard_spans.origin_low_byte = receipts.relay_node
            and receipts.received_at
            between heard_spans.first_heard_at
            and heard_spans.last_heard_at + {{ candidate_window() }}
    group by receipts.delivery_key
),

neighbour_matches as (
    select
        receipts.delivery_key,
        count(distinct neighbours.neighbour_pseudonym) as neighbour_count,
        min(neighbours.neighbour_pseudonym) as neighbour
    from receipts
    inner join {{ ref('stg_neighbor') }} as neighbours
        on
            neighbours.reporter_pseudonym = receipts.gateway_pseudonym
            and neighbours.neighbour_low_byte = receipts.relay_node
            and neighbours.received_at
            between receipts.received_at - {{ candidate_window() }} and receipts.received_at
    group by receipts.delivery_key
),

resolved as (
    select
        receipts.delivery_key,
        receipts.relay_node = receipts.origin_low_byte as direct_hop,
        case
            when receipts.relay_node = receipts.origin_low_byte then receipts.origin_pseudonym
            when candidate_matches.candidate_count = 1 then candidate_matches.candidate
        end as by_relay_node,
        case
            when neighbour_matches.neighbour_count = 1 then neighbour_matches.neighbour
        end as by_neighbor_info
    from receipts
    left join candidate_matches
        on candidate_matches.delivery_key = receipts.delivery_key
    left join neighbour_matches
        on neighbour_matches.delivery_key = receipts.delivery_key
),

links as (
    select
        delivery_key,
        by_relay_node is not null
        and by_neighbor_info is not null
        and by_relay_node <> by_neighbor_info as methods_disagree,
        direct_hop,
        coalesce(by_relay_node, by_neighbor_info) as relayer_pseudonym,
        case
            when by_relay_node is not null then 'relay_node'
            when by_neighbor_info is not null then 'neighbor_info'
        end as link_source
    from resolved
),

decided as (
    select
        packets.*,
        coalesce(links.methods_disagree and links.direct_hop, false)
            as direct_hop_contradicted,
        case when not links.methods_disagree then links.relayer_pseudonym end
            as relayer_pseudonym,
        case when not links.methods_disagree then links.link_source end as link_source,
        packets.rx_rssi >= -150 and packets.rx_rssi < 0
        and packets.rx_snr between -30 and 20 as readings_in_range
    from packets
    left join links
        on links.delivery_key = packets.delivery_key
)

select
    *,
    {{ link_hour('received_at') }} as hour,
    case
        when relayer_pseudonym is null then 'lnk_01'
        when relayer_pseudonym = gateway_pseudonym then 'lnk_03'
        when not coalesce(readings_in_range, false) then 'lnk_06'
    end as drop_rule
from decided
