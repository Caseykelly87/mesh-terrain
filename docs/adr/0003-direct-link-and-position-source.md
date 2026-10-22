# ADR 0003: Resolve links from relay_node per gateway, and test the MeshSTL broker before fixing the position source and area

Date: 2026-10-22
Status: proposed
Proposed by: ingest-eng
Decided by: Casey

Every number below is interim: it comes from the first 3.47 hours of a 48-hour capture of `msh/US/#` that started 2026-10-22T16:11:33Z, and it is replaced when the capture ends. Every weekly figure is extrapolated: observed count x 168 / 3.47 = observed x 48.48. Capture, decoder, commands and the full output tables are in `docs/adr/0003-spike/`.

## Context

The plan (section "Two constraints of the public broker") needs three answers before Phase 1 and 2 are built: how to identify the last radio hop into a gateway, where endpoint positions come from, and, because the interim yield is under 300 link-hours a week, whether to widen the area of interest. Terms:

- relay_node: one byte in each packet header, the low byte of the id of the node that last transmitted the packet by radio; a packet heard straight from its sender carries the sender's own low byte.
- NeighborInfo: a packet a node broadcasts listing the nodes it hears directly, each with a signal-to-noise ratio (SNR).
- precision_bits: how many of the 32 bits of a position's integer latitude and longitude the node keeps; at St. Louis 16 bits is a box of plus or minus 365 m north-south, 13 bits plus or minus 2.9 km.
- link-hour: one (relayer, gateway) pair in one clock hour that passes contract rules `lnk_01` to `lnk_05`: relayer resolved to exactly one node, both ends with a `usable` position (15 bits or more) within 30 minutes, different nodes, at least 3 packets, 500 m to 60 km apart.
- gateway: a node connected to the internet that uploads what it hears to an MQTT broker; the `rx_snr` and `rx_rssi` it reports describe the last hop.

The spike found a constraint the plan did not list. The public broker forwards the header of nearly every default-key packet but removes the payload unless the sender set the "OK to MQTT" bit: 96.2 percent of envelopes arrive header-only. `relay_node` survives on 1.5 percent of header-only envelopes and on 74.5 percent of envelopes with a payload, so in practice the link source exists only for opted-in senders. NeighborInfo never arrives.

## Measurements (interim)

| Measure | Value |
| --- | --- |
| (a) envelopes with non-zero `relay_node` | 3.8 percent nationally; 50.9 percent of those carry the sender's own low byte |
| (a) relayed receipts whose byte matches senders heard by the same gateway | one: 57.4 percent, more than one: 10.8 percent, none: 31.8 percent; against every sender in the capture it always matches many |
| (b) NeighborInfo | 0 packets, 0 pairs, no SNR to report |
| (c) `precision_bits`, national Position packets | 13: 40.2 percent, 14: 19.3, 15: 30.6, 16: 6.0, 12 or less: 3.8; nothing from 17 to 31 |
| (c) `precision_bits`, MSA Position packets | 13: 30.9 percent, 14: 18.6, 15: 27.1, 16: 11.2, 12 or less: 12.2 |
| (d) regional broker | MeshSTL (`https://meshstl.org`) publishes its own broker, `mqtt.meshstl.org:1883`, root topic `msh/US/MeshSTL`, with posted credentials, LongFast and LongTurbo on the default key and a MeshSTL channel with a published key. Precision not measured: subscribing to a broker outside the plan is Casey's call. |
| (d) regional topics on the public broker | `msh/US/MeshSTL` 702 packets per hour from 52 gateways; `msh/US/MO` 278 from 12; `msh/US/IL` 410 from 69 |
| MSA counts (CBSA 41180, seed from PR #7) | 69 nodes positioned inside, 31 usable; 33 gateways positioned inside, 16 usable; 243 relayed receipts at those gateways |

## Yield (interim, extrapolated)

Rows are link rules: (i) `relay_node` matched among senders the gateway heard, with the sender's own byte taken as a direct hop; (ii) NeighborInfo, where the byte must match exactly one of the gateway's reported neighbours; (iii) either, counted once, dropped when they disagree.

| Area | Rule | Per week, 500 m | Relayers | Gateways | Per week, 3 km | Relayers | Gateways |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MSA | (i) relay_node | 0 | 0 | 0 | 0 | 0 | 0 |
| MSA | (ii) NeighborInfo | 0 | 0 | 0 | 0 | 0 | 0 |
| MSA | (iii) combined | 0 | 0 | 0 | 0 | 0 | 0 |
| Missouri plus Illinois | (i) relay_node | 0 | 0 | 0 | 0 | 0 | 0 |
| Missouri plus Illinois | (ii) NeighborInfo | 0 | 0 | 0 | 0 | 0 | 0 |
| Missouri plus Illinois | (iii) combined | 0 | 0 | 0 | 0 | 0 | 0 |

Arithmetic for the MSA, combined rule: 243 relayed receipts, 180 resolved, 180 between different nodes, 11 with both ends usable within 30 minutes, 1 at 500 m to 60 km, 0 hours with 3 such receipts. 0 x 48.48 = 0 link-hours per week. Missouri plus Illinois follows the same path: 656, 436, 436, 15, 1, 0.

The (f) widening check triggers (0 against the 300 threshold), and widening does not help: the losses happen at the position rule, and Missouri plus Illinois loses them the same way. Without the position rule, the MSA has 17 (relayer, gateway, hour) groups with 3 or more resolved receipts in 3.47 hours, about 820 a week, across 9 pairs, 8 relayers and 7 gateways. Only 1 of those 9 pairs has both ends ever reporting 15 bits. Relaxing the window to 24 hours does not change the 3-packet result (sensitivity table in the spike README). The binding constraint is the public broker's position precision, not link resolution and not area size.

## Options

Link source:

1. `relay_node` matched among senders the same gateway heard. Advantage: carried by every opted-in packet. Cost: about 11 percent of receipts are ambiguous and 32 percent have no candidate.
2. NeighborInfo pairs. Advantage: explicit neighbour lists with SNR. Cost: absent from the public broker; usable only from a broker that keeps payloads.
3. Both, with `link_source` on `fct_rx_observation`. Advantage: matches the planned model and lets a second broker add NeighborInfo later. Cost: a second code path that is idle until such a broker exists.
4. Unknown-relayer fallback (plan Risks table): every receipt becomes a link of unknown origin; model gateway-side terrain only. Advantage: no resolution or relayer position needed. Cost: no path, so no terrain profile, and a much weaker result.

Position source:

- A. Public broker positions only. Advantage: no new source. Cost: interim yield is zero under the contract.
- B. MeshSTL broker. Advantage: a private broker does not strip payloads or cap precision, so it may carry NeighborInfo and positions at 32 bits, which `pos_04` already provides for. Cost: outside the plan's sources, someone else's server with no uptime promise, and full-precision positions of identifiable people raise the privacy stakes downstream.
- C. Both, with the source recorded per position row.

Area of interest:

- MSA as planned. Advantage: terrain, land cover and building work stay metro-sized. Cost: none extra; the yield is zero either way at present.
- Missouri plus Illinois. Advantage: 2.2 times the gateways and 2.7 times the relayed receipts. Cost: rasters and OSM for two states, and no gain in link-hours, because the position rule removes the same receipts.

## Decision

Proposed: link source option 3 (`relay_node` per gateway now, `link_source` column kept for NeighborInfo); position source C, conditional on Casey approving a read-only 24-hour capture of the MeshSTL broker that measures its precision and NeighborInfo volume before Phase 1 is built; area stays the MSA. If the 48-hour numbers and the MeshSTL capture both leave the yield near zero, the remaining choices are a change to `lnk_02` or `pos_01` (a contract change), or option 4 for the link source. Both go back to Casey with numbers.

## Consequences

- `lnk_01` reads: relayer resolved from `relay_node` among senders that gateway heard in the trailing 24 hours, with the sender's own byte taken as a direct hop; `hop_limit` stays unused. The strict reading without the direct-hop shortcut loses 3 percent of resolved receipts.
- `raw.decoded_packet` keeps header-only rows: they still count receipts per gateway for the funnel, even though they rarely carry `relay_node`.
- `ing_03` has no status for a payload removed by the broker; the spike reports it as `decrypt_failed` with reason `stripped`. Adding `stripped` is a contract change for Casey.
- A second broker means a second subscription, a `source` column on positions and `pos_04` in force; the privacy rules `prv_01` to `prv_03` stay as written and matter more.
- The write-up's data funnel gains a step the plan did not expect: header-only envelopes.
