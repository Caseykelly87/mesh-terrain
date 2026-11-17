# ADR 0003: Resolve links from relay_node on radio receipts, take positions from the MeshSTL broker under a stationarity window, and keep the MSA

Date: 2026-10-22, final numbers 2026-10-30, accepted 2026-10-31, area and position source amended 2026-11-08, stationarity rule amended 2026-11-17
Status: accepted

Amended 2026-11-08 by Casey's scope change (see Decision and Consequences): the widening trigger is retired, the position source is further MeshSTL captures under his approval rather than a standing subscription, and the data is a single capture from 2026-10-22 to 2026-10-25, snapshot `meshtastic-capture-2026-10-22` (ADR 0005).

Amended 2026-11-17 by Casey's decision on the stationarity rule (issue #44; see "Overlap amendment, 2026-11-17" below): a node's positions count as one place when their boxes overlap, instead of when they are the same box.

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
- relay_node: the low byte of the id of the node that last transmitted the packet by radio (0xFF when that byte is 0). A packet heard straight from its sender carries the sender's own low byte.
- NeighborInfo: a packet a node broadcasts listing the nodes it hears directly, with a signal-to-noise ratio (SNR) each. The public broker never forwards it; the MeshSTL broker does (104 packets in 24 hours, 11 reporters, 28 pairs, median SNR 6.0 dB).
- precision_bits: how many of the 32 bits of a position's integer latitude and longitude the node keeps. At St. Louis, 16 bits is a box of plus or minus 365 m north-south, 13 bits plus or minus 2.9 km. A position box is that square; two boxes overlap when they share ground, and then the distance is a range from 0 m to the larger box's diagonal.
- stationary node: a node whose every position in the capture is the same box. Since 2026-11-17 (Overlap amendment): a node whose usable positions inside the MSA, from either broker, all overlap one another, placed at its smallest box. The capture stands in for the trailing 7-day window the warehouse applies (with one capture, the window is the capture), but the spike also sees positions sent after a receipt, so it looks ahead; relayer candidates likewise come from the whole capture, not the trailing 24 hours.
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
- `lnk_02` gains the stationarity window: a node whose every position in the trailing 7 days is one box keeps that box; any other node keeps the 30-minute rule. Amended 2026-11-17: the rule reads only the node's usable positions inside the MSA, from either broker, for the stationarity test and the 30-minute fallback alike; a node whose boxes all overlap is stationary and is placed at its smallest box, and any other node keeps the 30-minute rule.
- `ing_03` keeps the status `stripped`; ADR 0005 changes what is stored for header-only copies.
- `analyze.py` accepts a negative portnum from wrong-key noise as decoded (3 packets in 63.59 hours); the Phase 1 decoder rejects portnum 0 or below.
- The capture containers had no built-in end time and ran under `--restart unless-stopped`. The public capture ran 15.5 hours past its window (to 2026-10-25T07:43Z) and MeshSTL 9.6 hours past its own, until Docker Desktop stopped. When Docker Desktop was started for a disk check on 2026-10-28, Docker restarted both: they wrote 4 minutes of data (the public fragment above, and 72 MeshSTL records) and the MeshSTL capture reconnected to a community broker outside the agreed window, until Casey stopped both and set their restart policy to `no`. `unless-stopped` restarts a container after any exit, a clean one included, and on every engine start unless it was stopped with `docker stop`; Docker Desktop quitting does not count. Fixed in PR #9: `capture.py` takes `END_AT` and `MAX_RAW_BYTES`, halts with a `HALTED.json` marker and exit 0, and refuses to start while the marker exists. Every Compose service runs with `restart: "no"` (PR #12). ADR 0005, as rescoped on 2026-11-08, replaces the marker and the restart policy with a capture run that always has a built-in end, writes a manifest and exits; `capture.py` is brought to that design in place and no capture is run before then.
- The data is a single capture, snapshot `meshtastic-capture-2026-10-22` under `C:/Users/Casey/mesh-terrain-data/snapshots/`, both brokers, 2026-10-22 to 2026-10-25, pseudonymised per ADR 0005 once the reproduction gate has passed. It is not treated as irreplaceable: no backup is required, and if it is lost the figures in this ADR are reproducible only by a new capture under a new snapshot id. Casey keeps an off-machine copy of the snapshot.
- The real-id archive was deleted on 2026-11-14, after the reproduction gate passed, under Casey's yes (PR #15, comments 6881270948 and 6882118525). 245 files, 3 379 978 111 bytes, were deleted (the two `abe9c41c1b90aa021` paths were hard links to one file, so the disk freed 3 378 978 274 bytes). Each was deleted only after its sha256, computed just before the delete, matched the list in the PR #15 body; no file differed and none was left.
  - 239 files were under `C:/Users/Casey/mesh-terrain-data/`: the public capture, the 2026-10-28 fragments, the Parquet conversion, the MeshSTL capture, a thin-mode test file, the gate reference outputs and the memory test.
  - The other 6 were tool logs that held real values, all deleted on 2026-11-14:
    - `public_results.md`, 6 312 bytes;
    - `public_run.log`, 6 319 bytes;
    - `agent-abe9c41c1b90aa021.jsonl`, 999 837 bytes;
    - `abe9c41c1b90aa021.output`, 999 837 bytes, a second name for the same file;
    - `a14082cb-cce2-460a-8ece-af2f3572a944.jsonl`, 11 233 999 bytes;
    - `b65382c2-4257-4139-8e70-2f6ac1f6652a.jsonl`, 4 580 568 bytes.

  No off-machine copy of the raw capture existed, so the raw capture no longer exists anywhere. The gate's raw-side figures, (b) and (c), can no longer be recomputed; the committed files under `docs/adr/0003-spike/reference/`, with `gate-2026-11-14.md`, are their record. After the deletion the snapshot passed its manifest check (0 problems, 195 data files).
- `lnk_06` was widened on 2026-11-14 by Casey's decision (option A, PR #15 comments 6883121025 and 6883294736) to RSSI [-150, 0] dBm and SNR [-30, 20] dB. A receipt with either reading outside its range does not enter `fct_rx_observation`; impossible values (a corrupt RSSI, an SNR beyond what the chip can report, a value that is not a number) are treated as missing and fail the rule. Every figure in this ADR was computed with the spike's limits, [-140, 0] dBm and [-25, 15] dB, and the reproduction gate keeps those limits so the figures still reproduce. The reproduction from the snapshot was rerun with the new limits, using `reproduce_from_snapshot.py` with `analyze.RSSI_RANGE_DBM` and `analyze.SNR_RANGE_DB` set to them. One more receipt passes `lnk_06` in the public full run and in the two public regional-root runs. The other two public runs, the MeshSTL runs and every link-hour and headline figure do not change.
  - In the public full run, receipts passing `lnk_06` go from 5 738 to 5 739, and the receipt goes on to the relayer-resolved and different-nodes steps (4 600 to 4 601 at each). Its relayer has no position in the capture, so it then fails `lnk_02` (2 325 to 2 326). The MSA relayer counts in `results.md` table (2a) and the no-position line each rise by one: 76 to 77 relayers, 33 to 34 with no decodable position, 31 to 32 of them sending other packets. The last funnel step stays at 277.
  - The two public regional-root runs show the same single receipt: relayer resolved goes from 4 597 to 4 598, their relayer count rises by one (74 to 75), and their last funnel step stays at 265.

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

## Overlap amendment, 2026-11-17

Casey's decision (issue #44, 2026-11-17), verbatim: "a node's usable positions (15 bits or more, inside the metro area, from either broker) count as the same place when their boxes overlap. A node is stationary when all of them share a common overlap, and it is placed at the smallest box (the most precise). Boxes that do not all overlap mean the node moved, and the existing 30-minute receipt rule applies."

Why: under the exact-box rule, a node that never moves but sends a blurred box on one channel and a precise box on another (for example a 13-bit box on the default channel beside a 16-bit box on another, or a coarse MapReport beside a precise Position) has two boxes and counts as moving. Overlapping boxes describe the same ground at different precisions, so the node is one place, best described by its most precise box.

What changes, against the rule of 2026-10-31:
- The rule reads only usable positions (15 bits or more) inside the MSA, for the stationarity test and the 30-minute fallback alike. Before, the stationarity test read every position from any area and at any precision, and a coarse or out-of-area box made a node non-stationary; the fallback's wording allowed any usable position, while the spike already read only usable positions inside the MSA for it, so the figures do not change on that account.
- Positions from both brokers count, as the old rule's wording already allowed; the per-broker figures in this ADR tested stationarity and the fallback within each broker's run.
- Boxes that overlap count as one place; the node is placed at its smallest box. The firmware stores a reduced-precision position as the centre of its box (meshtastic/firmware `src/mesh/PositionPrecision.cpp`, `truncateCoordinate`, commit 647831b; MapReport the same in `src/mqtt/MQTT.cpp`), and boxes of different precisions are nested grid cells. Two boxes overlap when their stored coordinates differ by less than the sum of the two half-widths, 2^(31 - `precision_bits`) x 1e-7 degrees each, in latitude and in longitude, computed on the integer values; boxes that only touch do not overlap. Overlapping cells nest, so boxes that overlap one another all contain the smallest one, and that box is unique. The spike's `boxes_overlap` computes the same test in floating point, which counts some touching boxes as overlapping, so it is not the definition here.

Effect on the figures: recomputed from the snapshot with `reproduce_from_snapshot.py`, the exact-box test replaced by the overlap rule and each broker's run reading that run's rows. The stationarity window row becomes 185 (4, 6, 6) at 500 m and 178 (3, 5, 5) at 3 km on the public broker's 63.53 hours, stays 228 (2, 2, 3) on its shared 24 hours, and becomes 617 (6, 6, 7) at 500 m on MeshSTL, with 309 (4, 4, 5) at 3 km unchanged. The 30-minute and gateway-only rows do not change. Stationary usable MSA nodes rise from 35 to 45 of 58 on the public broker's full run, from 25 to 35 of 41 in its shared 24 hours, and from 29 to 41 of 47 on MeshSTL. Of the 22 nodes added in the two full runs, 15 had one usable box with only coarse boxes beside it and 7 had usable boxes nested inside one another; none was added because of a box outside the MSA. The both-ends pairs at 500 m become public 6, MeshSTL 7, shared 5, union 8. No decision changes: the stationarity window gives more link-hours than the 30-minute rule everywhere, 185 against 100 on the public broker and 617 against 469 on MeshSTL at 500 m; MeshSTL still gives more than the public broker; and the 3 km figures are 178 public and 309 MeshSTL. The deduplicated table above keeps the figures of the rule of 2026-10-31.
