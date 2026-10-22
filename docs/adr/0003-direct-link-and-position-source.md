# ADR 0003: Resolve links from relay_node per gateway and take positions from the public broker, pending a MeshSTL broker check

Date: 2026-10-22
Status: proposed
Proposed by: ingest-eng
Decided by: Casey

Every number marked "interim" comes from the first part of a 48-hour capture that started 2026-10-22T16:11Z and is replaced when the capture completes. The capture, decoder and commands are in `docs/adr/0003-spike/`.

## Context

The plan (section "Two constraints of the public broker") needs two choices before Phase 1 and 2 can be built: how to identify the last radio hop into a gateway, and where endpoint positions come from. Terms used below:

- relay_node: one byte in each packet header, the low byte of the id of the node that last transmitted the packet over radio (firmware 2.6 and later); a packet heard straight from its sender carries the sender's own low byte.
- NeighborInfo: a packet a node broadcasts listing the nodes it heard directly, each with a signal-to-noise ratio.
- precision_bits: how many of the 32 bits of a position's integer latitude and longitude a node keeps; 16 bits places a node inside a box of about plus or minus 365 m north-south at St. Louis, 13 bits about plus or minus 2.9 km.
- link-hour: the unit of analysis, one (relayer, gateway) pair in one clock hour with at least 3 packets and both ends positioned (contract rules `lnk_01` to `lnk_05`).
- gateway: a node connected to the internet that uploads the packets it hears to an MQTT broker; the broker's `rx_snr` and `rx_rssi` are measured by the gateway on the last hop.

The spike found a third constraint the plan did not list. The public broker forwards the header of nearly every default-key packet but removes the payload unless the sender set the "OK to MQTT" bit (interim: 95.8 percent of envelopes arrive with no payload; of the senders seen, 82.8 percent arrive stripped only, 14.4 percent with payload only). Headers keep `from`, `id`, `rx_snr`, `rx_rssi`, `hop_start`, `hop_limit` (rewritten to 0) and `relay_node`, so link observations exist for every packet; positions exist only for opted-in nodes. NeighborInfo payloads were not seen at all (interim: 0 of 6200 decoded packets nationally), which fits the docs' statement that only certain portnums pass the public broker.

## Measurements

| Measure | Interim value (capture of 0.4 h, national unless stated) |
| --- | --- |
| (a) packets with non-zero `relay_node` | 4.2 percent of 240 864 headers; 50.5 percent of those equal the sender's own low byte (heard direct) |
| (a) `relay_node` resolves to one sender heard by the same gateway | one: 48.4 percent, more than one: 18.3 percent, none: 33.3 percent; against all senders in the capture it always matches many |
| (b) NeighborInfo | 0 packets, 0 pairs; median SNR not available |
| (c) `precision_bits` on Position packets | 13: 40.1 percent, 14: 19.4 percent, 15: 30.7 percent, 16: 6.0 percent, 12 and below: 3.7 percent; nothing between 17 and 31 |
| (c) MapReport `position_precision` | 14: 53.9 percent, 13: 21.6 percent, 15 to 19: 14.7 percent, 32: 8.7 percent |
| (d) regional broker | MeshSTL (`https://meshstl.org`) runs `mqtt.meshstl.org:1883` with posted credentials, root topic `msh/US/MeshSTL`, LongFast and LongTurbo on the default key, a secondary MeshSTL channel with a published key, and a Meshview at `meshview.meshstl.org`. Precision not measured: connecting to a broker outside the plan is a decision for Casey. |
| (d) regional topics on the public broker | `msh/US/MeshSTL`: 657 packets per hour from 20 gateways, 12.5 percent with `relay_node`; `msh/US/MO`: 197 per hour, 9 gateways; `msh/US/IL` (includes Chicago): 487 per hour, 31 gateways |
| MSA counts (CBSA 41180) | 13 nodes positioned inside, 5 of them usable (15 bits or more); 7 gateways positioned inside, 3 usable; 0 link-hours yet under `lnk_01` to `lnk_05` |

The half-widths at St. Louis latitude (38.63 N): 16 bits 365 m north-south and 285 m east-west; 15 bits 730 and 570 m; 14 bits 1459 and 1140 m; 13 bits 2918 and 2280 m.

## Options

Link source:

1. `relay_node` matched against the senders the same gateway has heard. Advantage: present on every header, stripped or not, so it covers all traffic. Cost: one byte is ambiguous (interim 18 percent of relayed packets match two or more candidates and are dropped), and a relayer never heard as a sender by that gateway cannot be resolved (interim 33 percent).
2. NeighborInfo pairs. Advantage: an explicit list of direct neighbours with SNR measured by the reporter. Cost: the public broker does not forward it; it is only available from a broker that keeps payloads, and NeighborInfo is sent at most every 15 minutes, so `lnk_04` (3 packets an hour) rarely holds.
3. Both, with `link_source` in {`relay_node`, `neighbor_info`} on `fct_rx_observation`. Advantage: the warehouse model already provides for it and the two sources check each other where both exist. Cost: two code paths; only worth it if option 2 has a feed.
4. Unknown-relayer fallback (plan Risks table): treat every receipt as a link of unknown origin and model gateway-side terrain only. Advantage: no resolution step. Cost: no path, so no terrain profile; a weaker project.

Position source:

- A. Public broker default-key positions. Advantage: already captured; 10 to 16 bits exactly as the plan assumed, with 15 and 16 bits on 36.7 percent of position packets (interim). Cost: only opted-in nodes report positions, and the box half-width is 365 m at best.
- B. MeshSTL broker (`mqtt.meshstl.org`, published credentials). Advantage: a private broker does not rewrite `hop_limit`, strip payloads or cap precision, so it may carry NeighborInfo and positions at the nodes' own precision (often 32 bits), which `pos_04` already provides for as `precise_source`. Cost: outside the plan's data sources; someone else's infrastructure with no uptime promise; full-precision positions of identifiable nodes raise the privacy bar on everything downstream.
- C. Both, with the source recorded per position row.

## Decision

Proposed: option 1 for the link source now, with the `relay_node` candidate set scoped to senders heard by the same gateway in the trailing 24 hours, and option A for positions, plus a read-only 24-hour capture of the MeshSTL broker approved by Casey to measure its precision and NeighborInfo volume before choosing between A and C. If the MeshSTL capture shows NeighborInfo and full-precision positions, the proposal becomes option 3 and C.

## Expected yield (interim arithmetic)

Regional packets per hour on the public broker (MeshSTL, MO, IL segments): 657 + 197 + 487 = 1341, about 225 000 per week. Chain of shares, each interim: `relay_node` set and not the gateway's own packet 28 percent (weighted across the three segments), resolves to exactly one candidate 48 percent, both ends with a usable position within 30 minutes 15 percent (proxy: 5 of 13 MSA nodes usable, squared), distance between 500 m and 60 km 80 percent (assumed). 225 000 x 0.28 x 0.48 x 0.15 x 0.80 = about 3 600 receipts per week. At roughly 6 receipts per active link-hour that is about 600 link-hours per week before `lnk_04`, perhaps 400 after it, and an unknown share over 3 km. The plan's risk line is 300 link-hours after two weeks; this estimate clears it on paper but rests on 25 minutes of data and a squared proxy for the position window, so it should be read as an order of magnitude only.

## Consequences

- `lnk_01` is finalised as: relayer resolved from `relay_node` against the senders the gateway heard in the trailing 24 hours; a packet whose `relay_node` equals the sender's own low byte is a direct link from the sender. `hop_limit` stays unused.
- The decode status set in `ing_03` has no value for a payload removed by the broker; the spike reports it under `decrypt_failed` with a reason. Casey decides whether to add `stripped` to the set (a contract change).
- `raw.decoded_packet` must keep header-only rows: they carry the link observation even without a payload.
- If the MeshSTL broker becomes a source, the ingester gets a second subscription, the position row gets a `source` column, and `pos_04` applies; the privacy rules in the contract (`prv_01` to `prv_03`) need no change but their enforcement matters more.
- NeighborInfo as a link source is off the table unless a payload-preserving broker is used.
