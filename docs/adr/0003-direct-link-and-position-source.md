# ADR 0003: Resolve links from relay_node on radio receipts, pick the position rule from the final yield table, and keep the MSA

Date: 2026-10-22
Status: proposed
Proposed by: ingest-eng
Decided by: Casey

Every number here is interim: it comes from the first 4.38 hours of a 48-hour capture that started 2026-10-22T16:11:33Z, and it is replaced when the capture ends. Every weekly figure is extrapolated: observed count x 168 / 4.38 = observed x 38.32. Commands and definitions are in `docs/adr/0003-spike/README.md`, the tables in `docs/adr/0003-spike/results.md`.

## Context

The plan (section "Two constraints of the public broker") needs a link source and a position rule before Phase 1 and 2 are built, and the widening check in the brief adds the area of interest. The capture gives zero link-hours a week under the contract rules in the MSA and in Missouri plus Illinois. Of 239 resolved receipts in the MSA, 157 fail because the relayer sent no decodable position in the first 4.4 hours (21 relayers, 19 of which sent NodeInfo, Telemetry or Routing), 50 because an end is below 15 bits, 11 on the 30-minute window, and 20 of the 21 survivors sit in overlapping position boxes. Ignoring positions, the MSA has about 920 groups a week with 3 or more resolved receipts across 12 pairs. Casey approved a 24-hour capture of the MeshSTL broker to see whether those relayers report positions there.

Terms:

- envelope: one MQTT message from the broker, a `ServiceEnvelope` wrapping one radio packet and the id of the gateway that uploaded it.
- header-only: an envelope whose packet header arrives but whose payload the broker removed. Its decode status is `stripped` (contract `ing_03`, approved 2026-10-22).
- via_mqtt: a header flag set when the gateway got the packet over MQTT, not by radio. Such a copy says nothing about a radio hop. They are 95 percent of all envelopes and are all header-only.
- radio delivery: an envelope that parses, has a sender, and has via_mqtt false. The reviewer's 82 812 and my 271 208 are the same definition: one clock hour (the hour-18 file, the gateway's own packets included) against 3.68 hours. `analyze.py` reproduces both.
- radio receipt: a distinct (gateway, sender, packet id) among radio deliveries, the gateway's own packets excluded. Every link figure below uses the radio receipts that carry `relay_node`: 32 051 of 37 672 so far.
- default key: the publicly documented key of the default channel, which anyone can decrypt.
- OK to MQTT bit: a flag a node sets in each packet to say it consents to uploading. 24.5 percent of packets that keep a payload lack it, so the bit alone does not explain which radio deliveries arrive header-only.
- relay_node: the low byte of the id of the node that last transmitted the packet by radio. A packet heard straight from its sender carries the sender's own low byte.
- NeighborInfo: a packet a node broadcasts listing the nodes it hears directly, with a signal-to-noise ratio (SNR) each. None arrived: only 7 portnums ever decode and none is unknown, which points to the broker's port allowlist ("only specific portnums are prioritized", `https://meshtastic.org/docs/software/integrations/mqtt/`).
- precision_bits: how many of the 32 bits of a position's integer latitude and longitude the node keeps. At St. Louis, 16 bits is a box of plus or minus 365 m north-south, 13 bits plus or minus 2.9 km. A position box is that square; two boxes overlap when they share ground, and then the distance is a range from 0 m to the larger box's diagonal.
- stationary node: a node whose every position in the capture is the same box. The 48-hour capture stands in for the 7-day window production would use.
- gateway: a node connected to the internet that uploads what it hears. Its `rx_snr` and `rx_rssi` describe the last radio hop.
- link-hour: one (relayer, gateway) pair in one clock hour that passes `lnk_01` to `lnk_06`: relayer resolved, both ends with a usable position (15 bits or more) within the position rule, different nodes, at least 3 packets, 500 m to 60 km on the median distance, signal in range.
- MSA and CBSA: a metropolitan statistical area is a Census grouping of counties around a city, identified by a core-based statistical area code; St. Louis is 41180, and the boundary is the merged seed `dbt/seeds/msa_boundary.csv`.

Evidence in brief, interim: `relay_node` is set on 85.5 percent of radio deliveries (gateway's own excluded) and resolves to one sender heard by the same gateway for 59.7 percent of radio receipts. MSA Position packets: 15 or 16 bits on 40.6 percent. The MeshSTL broker (`https://meshstl.org`, sections `#mqtt-settings` and `#channels`) publishes its server, credentials and a channel key; the credentials live outside the repo and the key is not used.

## Options

Link source:

1. `relay_node` matched among senders the same gateway heard by radio. Advantage: set on 85.5 percent of radio deliveries, payload or not. Cost: 9.7 percent of radio receipts match several candidates and 30.6 percent none.
2. NeighborInfo pairs. Advantage: explicit neighbour lists with SNR. Cost: the public broker does not forward them; MeshSTL may.
3. Both, with `link_source` on `fct_rx_observation`. Advantage: matches the planned model and takes NeighborInfo from MeshSTL if it arrives. Cost: one idle code path until then.
4. Unknown-relayer fallback (plan Risks table): model gateway-side terrain only. Advantage: needs no relayer position. Cost: no path, so no terrain profile.

Position rule, with the yield table (link-hours per week, relayers, gateways, pairs; MeshSTL cells pending its capture):

| Position rule | Public MSA, 500 m | Public MSA, 3 km | Public MO plus IL, 500 m | Public MO plus IL, 3 km | MeshSTL, 500 m | MeshSTL, 3 km |
| --- | --- | --- | --- | --- | --- | --- |
| keep 30 minutes (`lnk_02` as written) | 0 (0, 0, 0) | 0 (0, 0, 0) | 0 (0, 0, 0) | 0 (0, 0, 0) | pending | pending |
| stationarity window | 0 (0, 0, 0) | 0 (0, 0, 0) | 0 (0, 0, 0) | 0 (0, 0, 0) | pending | pending |
| gateway-only fallback | 575 (8, 6, 8) | n/a, no distance | 1 035 (13, 11, 13) | n/a, no distance | pending | pending |

- Keep 30 minutes. Advantage: the contract as written, and positions near the receipt in time. Cost: zero yield so far; 11 MSA receipts fail on the window alone.
- Stationarity window. Advantage: 24 of the 32 usable MSA nodes are stationary, so their one box covers every receipt. Cost: no gain so far, because the loss is upstream: relayers with no position at all, and overlapping boxes.
- Gateway-only fallback. Advantage: 575 link-hours a week in the MSA already, 8 relayers and 6 gateways. Cost: no path length and no terrain profile between the ends; the write-up models gateway-side terrain only.

Arithmetic, MSA, combined rule, 30 minutes: 316 radio receipts, 315 with signal in range, 239 resolved and not the gateway, 21 with both ends usable, 20 in overlapping boxes, 1 at 500 m to 60 km, 0 hours with 3 packets; 0 x 38.32 = 0. Gateway-only: 15 (relayer, gateway, hour) groups with 3 or more receipts and the gateway usable; 15 x 38.32 = 575.

Area of interest:

- MSA as planned. Advantage: metro-sized terrain and land-cover work. Cost: none beyond the plan.
- Missouri plus Illinois. Advantage: 2.7 times the receipts and 1.8 times the gateway-only link-hours. Cost: two states of rasters and OSM, and zero under the contract rules, because it fails at the same rules.

## Decision

Proposed, one bullet per question; Casey decides all three when the 48-hour and MeshSTL numbers are in.

- Link source: option 3, `relay_node` on radio receipts, with `link_source` kept for NeighborInfo.
- Position rule: decided from the final yield table above; if the first two rows stay at zero on both brokers, the gateway-only fallback.
- Area: the MSA.

## Consequences

- `lnk_01` reads: relayer resolved from `relay_node` among senders the gateway heard by radio in the trailing 24 hours, with the sender's own byte taken as a direct hop. via_mqtt envelopes are never receipts, and `hop_limit` stays unused.
- `ing_03` gains the status `stripped`, approved by Casey on 2026-10-22 and merged into the contract; `analyze.py` and the Phase 1 decoder keep it as its own status.
- Step 7 reports the time to first usable position and the count of relayers with no decodable position over the full 48 hours, so "not yet" and "not at all" are told apart before the position rule is chosen.
- If the gateway-only fallback is chosen, `int_link_window` has no `distance_m` for those rows and the features and validation marts need a gateway-only variant; that is a contract change for Casey.
- The write-up's funnel gains three steps the plan did not expect: via_mqtt copies, header-only envelopes, and relayers with no position.
