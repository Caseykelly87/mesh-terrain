# ADR 0003: Resolve links from relay_node on radio receipts, take positions from the MeshSTL broker under a stationarity window, and keep the MSA

Date: 2026-10-22, final numbers 2026-10-30
Status: proposed
Proposed by: ingest-eng
Decided by: Casey

The numbers are final. The public capture observed 63.594 hours (2026-10-22T16:11:36Z to 2026-10-25T07:43:14Z, plus a 4-minute fragment on 2026-10-28, see Consequences); the MeshSTL capture observed 23.998 hours (2026-10-23T22:09:00Z to 2026-10-24T22:08:51Z), and the brokers are compared only on the 24 hours both covered. Every rate is per observed hour. Every weekly figure is weekday-weighted: weekday hourly rate x 120 + weekend hourly rate x 48, local days. Commands and definitions are in `docs/adr/0003-spike/README.md`, the tables in `docs/adr/0003-spike/results.md`.

## Context

The plan (section "Two constraints of the public broker") needs a link source and a position rule before Phase 1 and 2 are built, and the widening check in the brief adds the area of interest. Over 63.59 hours the public broker gives 0.66 contract link-hours an hour in the MSA (110 a week) at 500 m and 107 a week at 3 km, from at most 2 relayers and 4 gateways. The interim 4.38-hour figure of zero was a short window: contract link-hours appear after 8 observed hours and grow to 42. The loss is still upstream of distance: of 4 604 resolved MSA receipts, 2 328 fail because the relayer never sent a decodable position in 63.59 hours (33 relayers, 31 of which sent other packets), 1 158 because an end is below 15 bits, 16 because the relayer was placed only outside the MSA, 430 on the window, and 355 of the 672 survivors sit in overlapping boxes. The MeshSTL broker, which Casey approved for a 24-hour capture to see whether those relayers report positions there, gives 3.08 contract link-hours an hour in the MSA (560 a week) at 500 m against 0.88 (177) from the public broker over the same 24 hours.

Terms:

- envelope: one MQTT message from the broker, a `ServiceEnvelope` wrapping one radio packet and the id of the gateway that uploaded it.
- header-only: an envelope whose packet header arrives but whose payload the broker removed. Its decode status is `stripped` (contract `ing_03`, approved 2026-10-22).
- via_mqtt: a header flag set when the gateway got the packet over MQTT, not by radio. Such a copy says nothing about a radio hop. On the public broker they are 95 percent of envelopes and 88.6 percent of bytes, and all are header-only. The MeshSTL broker sends none.
- radio delivery: an envelope that parses, has a sender, and has via_mqtt false. The reviewer's 82 812 is the radio deliveries in the clock hour 2026-10-22T18Z, the gateway's own packets included; `analyze.py` prints it in the hourly table.
- radio receipt: a distinct (gateway, sender, packet id) among radio deliveries, the gateway's own packets excluded. Every link figure uses the radio receipts that carry `relay_node`: 461 099 of 546 360 on the public broker.
- default key: the publicly documented key of the default channel, which anyone can decrypt.
- relay_node: the low byte of the id of the node that last transmitted the packet by radio. A packet heard straight from its sender carries the sender's own low byte.
- NeighborInfo: a packet a node broadcasts listing the nodes it hears directly, with a signal-to-noise ratio (SNR) each. The public broker never forwards it; the MeshSTL broker does (104 packets in 24 hours, 11 reporters, 28 pairs, median SNR 6.0 dB).
- precision_bits: how many of the 32 bits of a position's integer latitude and longitude the node keeps. At St. Louis, 16 bits is a box of plus or minus 365 m north-south, 13 bits plus or minus 2.9 km. A position box is that square; two boxes overlap when they share ground, and then the distance is a range from 0 m to the larger box's diagonal.
- stationary node: a node whose every position in the capture is the same box. The capture stands in for the 7-day window production would use.
- gateway: a node connected to the internet that uploads what it hears. Its `rx_snr` and `rx_rssi` describe the last radio hop.
- link-hour: one (relayer, gateway) pair in one clock hour that passes `lnk_01` to `lnk_06`: relayer resolved, both ends with a usable position (15 bits or more) within the position rule, different nodes, at least 3 packets, 500 m to 60 km on the median distance, signal in range.
- topic root: the segment after `msh/US` in a topic, a state or community name (`MO`, `IL`, `MeshSTL`) or the protocol version `2` when the gateway sets none.
- MSA and CBSA: a metropolitan statistical area is a Census grouping of counties around a city, identified by a core-based statistical area code; St. Louis is 41180, and the boundary is the merged seed `dbt/seeds/msa_boundary.csv`.

Evidence in brief: `relay_node` is set on 87.1 percent of public radio deliveries (gateway's own excluded) and 83.5 percent on MeshSTL, and resolves to one sender heard by the same gateway for 63.6 percent of public relayed receipts and 77.6 percent on MeshSTL. MSA Position packets at 15 bits or more: 35.7 percent public, 39.4 percent MeshSTL. 95.1 percent of the MSA's public relayed receipts arrive under the `msh/US/MeshSTL` root, 2.3 percent under `msh/US/MO`, none under `msh/US/IL`. The MeshSTL broker (`https://meshstl.org`, sections `#mqtt-settings` and `#channels`) publishes its server, credentials and a channel key; the credentials live outside the repo and the key is not used.

## Options

Link source:

1. `relay_node` matched among senders the same gateway heard by radio. Advantage: set on 83 to 87 percent of radio deliveries on both brokers, payload or not. Cost: 19.8 percent of public relayed receipts match several candidates and 16.6 percent none (12.5 and 9.9 on MeshSTL).
2. NeighborInfo pairs. Advantage: explicit neighbour lists with SNR. Cost: only MeshSTL forwards them, and alone they give 24 link-hours in 24 hours at 500 m from one pair, none at 3 km.
3. Both, with `link_source` on `fct_rx_observation`. Advantage: matches the planned model and keeps NeighborInfo as a cross-check. Cost: it adds no link-hour over `relay_node` in the measured 24 hours.
4. Unknown-relayer fallback (plan Risks table): model gateway-side terrain only. Advantage: needs no relayer position. Cost: no path, so no terrain profile.

Position source:

1. Public broker only. Advantage: no arrangement with a community operator. Cost: 110 contract link-hours a week at 500 m and 107 at 3 km in the MSA; a quarter of its radio deliveries arrive header-only, so positions are lost.
2. MeshSTL broker for the metro, public regional roots alongside. Advantage: no header stripping, NeighborInfo, 560 contract link-hours a week at 500 m and 281 at 3 km; it places 8 nodes the public broker never positions, while public positions add none of MeshSTL's 23 unplaced metro relayers. Cost: a standing subscription to a community broker, which needs the operators' agreement beyond the 24-hour courtesy note, and its own channel key stays unused, so 22.5 percent of its radio deliveries stay undecoded.

Position rule, link-hours a week in the MSA, combined link rule, weekday-weighted (distinct relayers, gateways, pairs):

| Position rule | Public, 63.59 h, 500 m | Public, 63.59 h, 3 km | Public, shared 24 h, 500 m | Public, shared 24 h, 3 km | MeshSTL, 24 h, 500 m | MeshSTL, 24 h, 3 km |
| --- | --- | --- | --- | --- | --- | --- |
| keep 30 minutes (`lnk_02` as written) | 110 (2, 4, 4) | 107 (1, 3, 3) | 177 (1, 2, 2) | 177 (1, 2, 2) | 560 (6, 6, 7) | 281 (4, 4, 5) |
| stationarity window | 168 (4, 6, 6) | 162 (3, 5, 5) | 228 (2, 2, 3) | 228 (2, 2, 3) | 617 (6, 6, 7) | 309 (4, 4, 5) |
| gateway-only fallback | 852 (23, 16, 28) | n/a, no distance | 962 (18, 10, 19) | n/a, no distance | 1 502 (26, 14, 28) | n/a, no distance |

- Keep 30 minutes. Advantage: the contract as written, positions near the receipt in time. Cost: on MeshSTL the 3 km figure is 281 a week, under the widening threshold of 300.
- Stationarity window. Advantage: 35 of 58 usable MSA nodes on the public broker and 29 of 48 on MeshSTL never move, so their one box covers every receipt; on MeshSTL it reaches 309 a week at 3 km. Cost: a contract change to `lnk_02`, and a node that moves after the window is mislocated until its next position.
- Gateway-only fallback. Advantage: 852 a week on the public broker and 1 502 on MeshSTL. Cost: no path length and no terrain profile, which is what the project tests.

Area of interest:

- MSA as planned. Advantage: metro-sized terrain and land-cover work. Cost: none beyond the plan.
- Missouri plus Illinois. Advantage: 3.6 times the radio receipts. Cost: two states of rasters and OSM for 6 more contract link-hours a week (116 against 110 on the public broker).

## Decision

Proposed, one bullet per question; Casey decides all four.

- Link source: option 3, `relay_node` on radio receipts, with `link_source` kept so NeighborInfo from MeshSTL can be compared.
- Position source: option 2, the MeshSTL broker for the metro, once its operators agree to a standing subscription, with the public broker's regional roots alongside (ADR 0005 item 7). Until they agree, option 1.
- Position rule: the stationarity window over the trailing 7 days, falling back to 30 minutes for a node that moved; the gateway-only fallback is not adopted.
- Area: the MSA.

## Consequences

- `lnk_01` reads: relayer resolved from `relay_node` among senders the gateway heard by radio in the trailing 24 hours, with the sender's own byte taken as a direct hop. via_mqtt envelopes are never receipts, and `hop_limit` stays unused.
- If the stationarity window is chosen, `lnk_02` gains it as a contract change for Casey: a node whose every position in the trailing 7 days is one box keeps that box; any other node keeps the 30-minute rule.
- `ing_03` keeps the status `stripped`; ADR 0005 changes what is stored for header-only copies.
- `analyze.py` accepts a negative portnum from wrong-key noise as decoded (3 packets in 63.59 hours); the Phase 1 decoder rejects portnum 0 or below.
- The capture containers had no built-in end time and ran under `--restart unless-stopped`. The public capture ran 15.5 hours past its window (to 2026-10-25T07:43Z) and MeshSTL 9.6 hours past its own, until Docker Desktop stopped. When Docker Desktop was started for a disk check on 2026-10-28, Docker restarted both: they wrote 4 minutes of data (the public fragment above, and 72 MeshSTL records) and the MeshSTL capture reconnected to a community broker outside the agreed window, until Casey stopped both and set their restart policy to `no`. `unless-stopped` restarts a container whenever the engine starts and after any exit, a clean one included. Fixed: `capture.py` takes `END_AT` and `MAX_RAW_BYTES`, halts with a `HALTED.json` marker and exit 0, and refuses to start while the marker exists; captures run with `--restart on-failure`; every Compose service is `restart: "no"` (PR #12). The Phase 1 ingester takes the same design (ADR 0005 item 1).
