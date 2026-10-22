# ADR 0003: Resolve links from relay_node on radio receipts, and measure MeshSTL before fixing the position source

Date: 2026-10-22
Status: proposed
Proposed by: ingest-eng
Decided by: Casey

Every number here is interim: it comes from the first 4.05 hours of a 48-hour capture that started 2026-10-22T16:11:33Z, and it is replaced when the capture ends. Every weekly figure is extrapolated: observed count x 168 / 4.05 = observed x 41.47. Full tables, rule definitions and commands are in `docs/adr/0003-spike/README.md`.

## Context

The plan (section "Two constraints of the public broker") needs a link source and a position source before Phase 1 and 2 are built. The interim capture triggered the widening check, so the area of interest is a third question. It finds zero link-hours a week under the contract rules, in the MSA and in Missouri plus Illinois. The main cause is that most relayers never send a position at all; position precision and area size come second.

Terms:

- envelope: one MQTT message from the broker, a `ServiceEnvelope` wrapping one radio packet and the id of the gateway that uploaded it.
- header-only: an envelope whose packet header (sender, packet id, signal, `relay_node`) arrives but whose payload the broker removed.
- via_mqtt: a header flag set when the gateway got the packet over MQTT, not by radio. Such a copy says nothing about a radio hop.
- default key: the publicly documented encryption key of the default channel, which anyone can decrypt.
- OK to MQTT bit: a flag a node sets in each packet to say it consents to its packets being uploaded.
- relay_node: the low byte of the id of the node that last transmitted the packet by radio. A packet heard straight from its sender carries the sender's own low byte.
- NeighborInfo: a packet a node broadcasts listing the nodes it hears directly, each with its signal-to-noise ratio (SNR).
- precision_bits: how many of the 32 bits of a position's integer latitude and longitude the node keeps. At St. Louis, 16 bits is a box of plus or minus 365 m north-south and 13 bits plus or minus 2.9 km.
- gateway: a node connected to the internet that uploads what it hears. Its `rx_snr` and `rx_rssi` describe the last radio hop.
- link-hour: one (relayer, gateway) pair in one clock hour that passes `lnk_01` to `lnk_06`: relayer resolved, both ends with a usable position (15 bits or more) within 30 minutes, different nodes, at least 3 packets, 500 m to 60 km apart, signal in range.
- MSA and CBSA: a metropolitan statistical area is a Census grouping of counties around a city. Its core-based statistical area (CBSA) code for St. Louis is 41180, and the boundary is the merged seed `dbt/seeds/msa_boundary.csv`.

## Evidence (interim)

| Measure | Value |
| --- | --- |
| (a) radio envelopes, gateway's own packets excluded | 176 841; 49.5 percent header-only; `relay_node` set on 85.5 percent, and on 93.8 percent of header-only ones |
| (a) via_mqtt copies | 5 838 465, 95 percent of all envelopes; all header-only; `relay_node` set on 0.02 percent |
| (a) byte resolution on distinct radio receipts | one sender heard by the same gateway: 59.3 percent; several: 9.7; none: 31.1 |
| (b) NeighborInfo | 0 packets. Only 7 portnums ever arrived decoded and none was unknown, which points to a broker-side port allowlist (the docs say "only specific portnums are prioritized"), not to low NeighborInfo adoption |
| (c) precision_bits, MSA Position packets | 13: 31.5 percent; 14: 17.1; 15 or 16: 40.0; 12 or less: 11.4 |
| (d) regional source | MeshSTL (`https://meshstl.org`) runs `mqtt.meshstl.org:1883`, root topic `msh/US/MeshSTL`, posted credentials, and a channel with a published key. Not measured, see Options |
| MSA counts | 70 nodes positioned (32 usable), 33 gateways positioned (16 usable), 278 radio receipts with `relay_node` |

Why a radio envelope arrives header-only is not established. 24.5 percent of the packets that keep a payload lack the OK to MQTT bit, so the bit alone does not decide it.

Yield, link-hours per week (relayers, gateways):

| Rule | MSA, 500 m | MSA, 3 km | MO plus IL, 500 m | MO plus IL, 3 km |
| --- | --- | --- | --- | --- |
| (i) relay_node | 0 (0, 0) | 0 (0, 0) | 0 (0, 0) | 0 (0, 0) |
| (ii) NeighborInfo | 0 (0, 0) | 0 (0, 0) | 0 (0, 0) | 0 (0, 0) |
| (iii) combined | 0 (0, 0) | 0 (0, 0) | 0 (0, 0) | 0 (0, 0) |

Arithmetic, MSA, combined rule: 278 radio receipts, 277 with signal in range, 207 with the relayer resolved and not the gateway, 13 with both ends usable within 30 minutes, 1 at 500 m to 60 km, 0 hours with 3 packets; 0 x 41.47 = 0. Missouri plus Illinois: 755, 750, 496, 18, 1, 0.

Where the 207 resolved MSA receipts fail:

- 140: the relayer never sent a decodable position. These are 21 relayers, and 19 of them did send NodeInfo, Telemetry or Routing packets.
- 44: an end is below 15 bits (33 relayer, 11 gateway).
- 10: usable positions exist but fall outside the 30-minute window.
- 12 of the 13 survivors: the two ends are 0 m apart, the same position box, and `lnk_05` removes them.

Ignoring positions, the MSA has 20 (relayer, gateway, hour) groups with 3 or more receipts, about 829 a week, across 9 pairs. For 6 of those pairs the relayer never sent a position.

## Options

Link source:

1. `relay_node` matched among senders the same gateway heard by radio. Advantage: set on 85.5 percent of radio receipts, payload or not. Cost: 9.7 percent ambiguous and 31.1 percent with no candidate.
2. NeighborInfo pairs. Advantage: explicit neighbour lists with SNR. Cost: the public broker does not forward them.
3. Both, with `link_source` on `fct_rx_observation`. Advantage: matches the planned model and leaves room for a second broker. Cost: one idle code path for now.
4. Unknown-relayer fallback (plan Risks table): model gateway-side terrain only. Advantage: needs no relayer position, which is the main loss. Cost: no path, so no terrain profile.

Position source:

- A. Public broker positions, including MapReports. Advantage: no new source. Cost: zero yield at present.
- B. MeshSTL broker and its published-key channel. Advantage: may carry positions that relayers share only there, and at full precision. Cost: outside the plan, and someone else's server. Decrypting a non-default channel is a stop condition in the brief. More precision cannot fix a node that never sends a position, or two nodes at the same site.
- C. Both, with the source recorded per position row.

Area of interest:

- MSA as planned. Advantage: metro-sized terrain and land-cover work. Cost: none beyond the plan.
- Missouri plus Illinois. Advantage: 2.7 times the receipts. Cost: two states of rasters and OSM, and still zero, because it fails at the same rules.

## Decision

- Link source, proposed: option 3, `relay_node` on radio receipts, with `link_source` kept.
- Position source, proposed: A now, plus a read-only 24-hour MeshSTL measurement approved by Casey before choosing C.
- Area, proposed: the MSA.

## Consequences

- `lnk_01` reads: relayer resolved from `relay_node` among senders the gateway heard by radio in the trailing 24 hours, with the sender's own byte taken as a direct hop. via_mqtt envelopes are never receipts, and `hop_limit` stays unused.
- `ing_03` has no status for a payload the broker removed. The spike reports it as `decrypt_failed` with reason `stripped`. Adding `stripped` would change the contract, so Casey decides.
- If the 48-hour numbers and MeshSTL still leave relayers unpositioned, the remaining routes are option 4 or a change to `lnk_02`. Both are contract-level and go to Casey with numbers.
- The write-up's funnel gains two steps the plan did not expect: via_mqtt copies, and relayers with no position.
