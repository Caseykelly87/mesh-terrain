# ADR 0003: Resolve links from relay_node on radio receipts, take positions from the MeshSTL broker under a stationarity window, and keep the MSA

Date: 2026-10-22, final numbers 2026-10-30, accepted 2026-10-31, area and position source amended 2026-11-08
Status: accepted

Amended 2026-11-08 by Casey's scope change (see Decision and Consequences): the widening trigger is retired, the position source is further MeshSTL captures under his approval rather than a standing subscription, and the data is a single capture from 2026-10-22 to 2026-10-25, snapshot `meshtastic-capture-2026-10-22` (ADR 0005).
Proposed by: ingest-eng
Decided by: Casey

The numbers are final. The public capture observed 63.594 hours (2026-10-22T16:11:36Z to 2026-10-25T07:43:14Z, plus a 4-minute fragment on 2026-10-28, see Consequences); the MeshSTL capture observed 23.998 hours (2026-10-23T22:09:00Z to 2026-10-24T22:08:51Z), and the brokers are compared only on the 24 hours both covered. Every rate is per observed hour. Every weekly figure is weekday-weighted: weekday hourly rate x 120 + weekend hourly rate x 48, local days. Commands and definitions are in `docs/adr/0003-spike/README.md`, the tables in `docs/adr/0003-spike/results.md`.

## Context

The plan (section "Two constraints of the public broker") needs a link source and a position rule before Phases 1 and 2 are built, and the widening check adds the area of interest. Over 63.59 observed hours the public broker gives 0.66 contract link-hours an hour in the MSA (110 a week) at 500 m, and the loss is upstream of distance: of 4 604 resolved MSA receipts, 2 328 fail because the relayer never sent a decodable position. The MeshSTL broker, captured for 24 hours with Casey's approval, gives 3.08 an hour (560 a week) against 0.88 (177) from the public broker over the same hours.

Terms:

- envelope: one MQTT message from the broker, a `ServiceEnvelope` wrapping one radio packet and the id of the gateway that uploaded it.
- header-only: an envelope whose packet header arrives but whose payload the broker removed. Its decode status is `stripped` (contract `ing_03`, approved 2026-10-22).
- via_mqtt: a header flag set when the gateway got the packet over MQTT, not by radio. Such a copy says nothing about a radio hop. On the public broker they are 95 percent of parsed envelopes (92.2 percent of all `e` records) and 88.6 percent of bytes, and all but 1 204 of 74.3 million are header-only. The MeshSTL broker sends none.
- radio delivery: an envelope that parses, has a sender, and has via_mqtt false. The reviewer's 82 812 is the radio deliveries in the clock hour 2026-10-22T18Z, the gateway's own packets included; `analyze.py` prints it in the hourly table.
- radio receipt: a distinct (gateway, sender, packet id) among radio deliveries, the gateway's own packets excluded. Every link figure uses the radio receipts that carry `relay_node`: 461 099 of 546 360 on the public broker.
- default key: the publicly documented key of the default channel, which anyone can decrypt.
- relay_node: the low byte of the id of the node that last transmitted the packet by radio. A packet heard straight from its sender carries the sender's own low byte.
- NeighborInfo: a packet a node broadcasts listing the nodes it hears directly, with a signal-to-noise ratio (SNR) each. The public broker never forwards it; the MeshSTL broker does (104 packets in 24 hours, 11 reporters, 28 pairs, median SNR 6.0 dB).
- precision_bits: how many of the 32 bits of a position's integer latitude and longitude the node keeps. At St. Louis, 16 bits is a box of plus or minus 365 m north-south, 13 bits plus or minus 2.9 km. A position box is that square; two boxes overlap when they share ground, and then the distance is a range from 0 m to the larger box's diagonal.
- stationary node: a node whose every position in the capture is the same box. The capture stands in for the trailing 7-day window the warehouse applies (with one capture, the window is the capture), but the spike also sees positions sent after a receipt, so it looks ahead; relayer candidates likewise come from the whole capture, not the trailing 24 hours.
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
2. MeshSTL broker for the metro, public regional roots alongside. Advantage: no header stripping, NeighborInfo, 560 contract link-hours a week at 500 m and 281 at 3 km; in the shared 24 hours it places 8 nodes the public broker does not, while public positions add none of MeshSTL's 23 unplaced metro relayers. Cost: a standing subscription to a community broker, which needs the operators' agreement beyond the 24-hour courtesy note, and its own channel key stays unused, so 22.5 percent of its radio deliveries stay undecoded.

Position rule, link-hours a week in the MSA, combined link rule, weekday-weighted (distinct relayers, gateways, pairs):

| Position rule | Public, 63.59 h, 500 m | Public, 63.59 h, 3 km | Public, shared 24 h, 500 m | Public, shared 24 h, 3 km | MeshSTL, 24 h, 500 m | MeshSTL, 24 h, 3 km |
| --- | --- | --- | --- | --- | --- | --- |
| keep 30 minutes (`lnk_02` as written) | 110 (2, 4, 4) | 107 (1, 3, 3) | 177 (1, 2, 2) | 177 (1, 2, 2) | 560 (6, 6, 7) | 281 (4, 4, 5) |
| stationarity window | 168 (4, 6, 6) | 162 (3, 5, 5) | 228 (2, 2, 3) | 228 (2, 2, 3) | 617 (6, 6, 7) | 309 (4, 4, 5) |
| gateway-only fallback | 852 (23, 16, 28) | n/a, no distance | 962 (18, 10, 19) | n/a, no distance | 1 502 (26, 14, 28) | n/a, no distance |

- Keep 30 minutes. Advantage: the contract as written, positions near the receipt in time. Cost: on MeshSTL the 3 km figure is 281 a week, under the widening threshold of 300.
- Stationarity window. Advantage: 35 of 58 usable MSA nodes on the public broker and 29 of 48 on MeshSTL never move, so their one box covers every receipt; on MeshSTL it reaches 309 a week at 3 km, though that rests on a weekday rate from one Friday evening (6.85 hours) and is 294 unweighted. Cost: a contract change to `lnk_02`, and a node that moves after the window is mislocated until its next position.
- Gateway-only fallback. Advantage: 852 a week on the public broker and 1 502 on MeshSTL. Cost: no path length and no terrain profile, which is what the project tests.

Area of interest:

- MSA as planned. Advantage: metro-sized terrain and land-cover work. Cost: none beyond the plan.
- Missouri plus Illinois. Advantage: 3.6 times the radio receipts, and under the stationarity rule 364 a week at 500 m and 357 at 3 km against the MSA's 168 and 162 on the public broker, which clears the widening threshold of 300 where the MSA does not. Cost: two states of rasters and OSM; under the 30-minute rule as written it adds only 6 a week (116 against 110).

## Decision

Casey accepted all four on 2026-10-31.

- Link source: option 3, `relay_node` on radio receipts, with `link_source` kept so NeighborInfo from MeshSTL can be compared.
- Position source: option 2, the MeshSTL broker for the metro, with the public broker's regional roots alongside (ADR 0005 item 7). As decided on 2026-10-31 this meant a standing subscription once the operators agreed; since 2026-11-08 there is no subscription of any kind. The MeshSTL positions in use are those of the single capture, taken with the operators' courtesy agreement, and any further MeshSTL capture is one run under a new snapshot id with Casey's explicit approval first.
- Position rule: the stationarity window over the trailing 7 days, falling back to 30 minutes for a node that moved; the gateway-only fallback is not adopted.
- Area: the MSA. On 2026-10-31 widening to Missouri plus Illinois was kept as a documented trigger (under 300 contract link-hours a week at 3 km after two weeks of production ingestion). On 2026-11-08 Casey retired the trigger with the scope change: there is no production ingestion for it to measure, and the area is the MSA. The spike's figures stay as the record: 162 a week in the MSA and 357 in Missouri plus Illinois on the public broker under the stationarity rule, and 309 in the MSA on MeshSTL.

## Consequences

- `lnk_01` and `lnk_02` are changed in `docs/data-quality-contract.md` in the same PR. `lnk_01` reads: relayer resolved from `relay_node` among senders the gateway heard by radio in the trailing 24 hours, with the sender's own byte taken as a direct hop. via_mqtt envelopes are never receipts, and `hop_limit` stays unused.
- `lnk_02` gains the stationarity window: a node whose every position in the trailing 7 days is one box keeps that box; any other node keeps the 30-minute rule.
- `ing_03` keeps the status `stripped`; ADR 0005 changes what is stored for header-only copies.
- `analyze.py` accepts a negative portnum from wrong-key noise as decoded (3 packets in 63.59 hours); the Phase 1 decoder rejects portnum 0 or below.
- The capture containers had no built-in end time and ran under `--restart unless-stopped`. The public capture ran 15.5 hours past its window (to 2026-10-25T07:43Z) and MeshSTL 9.6 hours past its own, until Docker Desktop stopped. When Docker Desktop was started for a disk check on 2026-10-28, Docker restarted both: they wrote 4 minutes of data (the public fragment above, and 72 MeshSTL records) and the MeshSTL capture reconnected to a community broker outside the agreed window, until Casey stopped both and set their restart policy to `no`. `unless-stopped` restarts a container after any exit, a clean one included, and on every engine start unless it was stopped with `docker stop`; Docker Desktop quitting does not count. Fixed in PR #9: `capture.py` takes `END_AT` and `MAX_RAW_BYTES`, halts with a `HALTED.json` marker and exit 0, and refuses to start while the marker exists. Every Compose service runs with `restart: "no"` (PR #12). ADR 0005, as rescoped on 2026-11-08, replaces the marker and the restart policy with a capture run that always has a built-in end, writes a manifest and exits; `capture.py` is brought to that design in place and no capture is run before then.
- The data is a single capture, snapshot `meshtastic-capture-2026-10-22` under `C:/Users/Casey/mesh-terrain-data/snapshots/`, both brokers, 2026-10-22 to 2026-10-25, pseudonymised per ADR 0005 once the reproduction gate has passed. It is not treated as irreplaceable: no backup is required, and if it is lost the figures in this ADR are reproducible only by a new capture under a new snapshot id. The real-id archive it was reduced from is deleted after the gate, under Casey's yes, and that date is recorded here when it happens.

## Deduplicated figures, 2026-11-14

`docs/adr/0003-spike/results.md` stays as the historical record. The figures below are the same measures on the pseudonymised snapshot, which the reproduction gate proved equal to the archive under the snapshot's four design rules (`docs/adr/0003-spike/reproduction-2026-11.md`). They differ from `results.md` for one main reason: 80 percent of the public broker's deliveries before merging, and half of MeshSTL's, are byte-identical repeats. The spike counted every repeat, and later repeats of Position and MapReport packets gave it fresh position samples. The snapshot keeps the first sighting only, and a delivery both brokers carried is timed at its first sighting on either broker. The fragment of 2026-10-28 is also left out.

Position rule, link-hours a week in the MSA, combined link rule, weekday-weighted (distinct relayers, gateways, pairs), deduplicated:

| Position rule | Public, 63.53 h, 500 m | Public, 63.53 h, 3 km | Public, shared 24 h, 500 m | Public, shared 24 h, 3 km | MeshSTL, 24 h, 500 m | MeshSTL, 24 h, 3 km |
| --- | --- | --- | --- | --- | --- | --- |
| keep 30 minutes (`lnk_02` as written) | 100 (1, 3, 3) | 100 (1, 3, 3) | 177 (1, 2, 2) | 177 (1, 2, 2) | 469 (6, 6, 7) | 281 (4, 4, 5) |
| stationarity window | 159 (3, 5, 5) | 159 (3, 5, 5) | 228 (2, 2, 3) | 228 (2, 2, 3) | 499 (6, 6, 7) | 309 (4, 4, 5) |
| gateway-only fallback | 744 (22, 16, 27) | n/a, no distance | 879 (16, 9, 17) | n/a, no distance | 1 465 (24, 13, 26) | n/a, no distance |

No decision changes:

- **Link source:** relayer resolution is unchanged. Of the radio receipts with `relay_node` at MSA gateways, the same number resolve: public 4 600 of 5 755, MeshSTL 3 184 of 3 863.
- **Position source:** MeshSTL still gives far more link-hours than the public broker, 469 against 100 a week at 500 m and 281 against 100 at 3 km.
- **Position rule:** the stationarity window still gives at least as many link-hours as the 30-minute rule everywhere, 159 against 100 on the public broker and 499 against 469 on MeshSTL at 500 m.
- **Area:** the MSA stays. The 3 km figures under the stationarity rule are 159 public and 309 MeshSTL, and the widening trigger was retired on 2026-11-08 in any case.
- **The 8 both-ends pairs:** the union of the pairs positioned at both ends under the stationarity rule at 500 m (`docs/measurements-2026-11-08.md`) is still 8: public 5 (was 6), MeshSTL 7, shared 4 (was 5). From the counts alone (shared falls by one while the union holds), the pair the public broker no longer reaches is inferred to be one MeshSTL carries. The union is computed on pseudonyms, so it is checked by count, not against the spike's real-id pair list.
