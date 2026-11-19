# ADR 0003: Resolve links from relay_node on radio receipts, take positions from the MeshSTL broker under a stationarity window, and keep the MSA

Date: 2026-10-22, final numbers 2026-10-30, accepted 2026-10-31, area and position source amended 2026-11-08, stationarity rule and link rules amended 2026-11-17
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
- stationary node: a node whose every position in the capture is the same box. Since 2026-11-17 (Overlap amendment): a node whose usable positions inside the MSA, from either broker, all overlap one another, placed at its smallest box. Since the Link-rule amendment below, positions within `STATIONARY_FLOOR_M` of each other also count as one place (pairwise, never chained), and a stationary node's box is its smallest box or, when the floor merges fixes, the rectangle around them; a link-hour chooses a stationary end's box once. The capture stands in for the trailing 7-day window the warehouse applies (with one capture, the window is the capture), but the spike also sees positions sent after a receipt, so it looks ahead; relayer candidates likewise come from the whole capture, not the trailing 24 hours.
- gateway: a node connected to the internet that uploads what it hears. Its `rx_snr` and `rx_rssi` describe the last radio hop.
- link-hour: one (relayer, gateway) pair in one clock hour that passes `lnk_01` to `lnk_06`: relayer resolved, both ends with a usable position (15 bits or more) within the position rule, different nodes, at least 3 packets, 500 m to 60 km on the median distance, signal in range. As the spike computes it and as `lnk_04` and `lnk_05` say since 2026-11-17: each receipt must lie between 500 m and 60 km, only those count toward the 3 packets, and their median must reach 500 m.
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
- `lnk_06` was widened on 2026-11-14 by Casey's decision (option A, PR #15 comments 6883121025 and 6883294736) to RSSI [-150, 0] dBm and SNR [-30, 20] dB (since 2026-11-17, [-150, 0) dBm, an RSSI of 0 being treated as missing; Link-rule amendment below). A receipt with either reading outside its range does not enter `fct_rx_observation`; impossible values (a corrupt RSSI, an SNR beyond what the chip can report, a value that is not a number) are treated as missing and fail the rule. Every figure in this ADR was computed with the spike's limits, [-140, 0] dBm and [-25, 15] dB, and the reproduction gate keeps those limits so the figures still reproduce. The reproduction from the snapshot was rerun with the new limits, using `reproduce_from_snapshot.py` with `analyze.RSSI_RANGE_DBM` and `analyze.SNR_RANGE_DB` set to them. One more receipt passes `lnk_06` in the public full run and in the two public regional-root runs. The other two public runs, the MeshSTL runs and every link-hour and headline figure do not change.
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
- Boxes that overlap count as one place; the node is placed at its smallest box. The firmware stores a reduced-precision position as the centre of its box (meshtastic/firmware `src/mesh/PositionPrecision.cpp`, `truncateCoordinate`, commit 647831b; MapReport the same in `src/mqtt/MQTT.cpp`), and boxes of different precisions are nested grid cells. Two boxes overlap when their stored coordinates differ by less than the sum of the two half-widths, 2^(31 - `precision_bits`) x 1e-7 degrees each, in latitude and in longitude, computed on the integer values; boxes that only touch do not overlap. Overlapping cells nest, so boxes that overlap one another all contain the smallest one, and that box is unique (without the floor of the Link-rule amendment below). The spike's `boxes_overlap` computes the same test in floating point, which counts some touching boxes as overlapping, so it is not the definition here.

Effect on the figures: recomputed from the snapshot with `reproduce_from_snapshot.py`, the exact-box test replaced by the overlap rule and each broker's run reading that run's rows. The stationarity window row becomes 185 (4, 6, 6) at 500 m and 178 (3, 5, 5) at 3 km on the public broker's 63.53 hours, stays 228 (2, 2, 3) on its shared 24 hours, and becomes 617 (6, 6, 7) at 500 m on MeshSTL, with 309 (4, 4, 5) at 3 km unchanged. The 30-minute and gateway-only rows do not change. Stationary usable MSA nodes rise from 35 to 45 of 58 on the public broker's full run, from 25 to 35 of 41 in its shared 24 hours, and from 29 to 41 of 47 on MeshSTL. Of the 22 nodes added in the two full runs, 15 had one usable box with only coarse boxes beside it and 7 had usable boxes nested inside one another; none was added because of a box outside the MSA. The both-ends pairs at 500 m become public 6, MeshSTL 7, shared 5, union 8. No decision changes: the stationarity window gives more link-hours than the 30-minute rule everywhere, 185 against 100 on the public broker and 617 against 469 on MeshSTL at 500 m; MeshSTL still gives more than the public broker; and the 3 km figures are 178 public and 309 MeshSTL. The deduplicated table above keeps the figures of the rule of 2026-10-31.

## Link-rule amendment, 2026-11-17

Casey's answers on issue #55 (comment 6959184944), verbatim, settle the points the conformance fixture needs before its expected outputs are written by hand:

> Q1 agree: item 4 implements the contract as written on the merged snapshot; the spike's per-run figures stay the record, and the pair count is whatever the contract gives.
> Q2 agree: overlapping relayer and gateway boxes fail lnk_05 by the integer test.
> Q3 agree: per receipt first, then the median.
> Q4 geodesic (::geography), not EPSG:26915: exact, matches the spike, and avoids the UTM 15/16 boundary through Metro East. State it in lnk_05.
> Q5 agree: nearest in time, the earlier on a tie, 30 minutes and 7 days inclusive.
> Q6 drop the link-hour when an end used more than one box within it, and count those hours in mart_data_quality. This matches the per-pair rule for moving nodes in the engine.
> Q7 agree: lnk_06 reads [-150, 0) dBm, 0 treated as missing.
> Q8 agree, all three.

The questions are in issue #55's body, section 2. Q8's three items are the `percentile_cont(0.5)` median, the first copy kept in `stg_packet` by `received_at`, and the inclusive 7-day boundary, written under Q5.

What changes in the contract:
- `ing_06`: staging keeps the first copy of a packet for one gateway by `received_at`.
- `lnk_02`: the fallback takes the nearest position, the earlier on a tie, and 30 minutes and 7 days are inclusive. A link-hour where an end used more than one box is dropped and counted in `mart_data_quality`.
- `lnk_04`: packets are counted after `lnk_05`'s per-receipt test, and medians are `percentile_cont(0.5)` over those receipts.
- `lnk_05`: each receipt's distance is the WGS84 geodesic. A receipt passes between 500 m and 60 000 m inclusive, and only if the two ends' boxes do not overlap. The link-hour's median must reach 500 m.
- `lnk_06`: RSSI in [-150, 0) dBm.
- `pos_04`: a position of 20 bits or more is flagged `precise_source` exactly when `seen_meshstl` is true on its delivery (Casey, issue #55, D10).
- `lnk_02` and `lnk_04`, Q9: the one-box test reads only the receipts an hour counts, in a stated order.
- `lnk_02`: the floor `STATIONARY_FLOOR_M`, pairwise and never chained; the box around merged fixes; one box per end for the hour, chosen before `lnk_05`; and which box `lnk_05`'s two tests use.

The spike's figures in this ADR are not recomputed: per Q1 they stay the record, computed by the spike's own rules (run by run, looking ahead, floating-point overlap, the geodesic distance, the older `lnk_06` limits).

Casey's follow-up on issue #55 (comment 6959592431), verbatim, items 1 to 3:

> 1. Q9: (a). The box test reads only the receipts the hour counts. Order as you wrote it: per-receipt tests, then one box per end over those receipts or the hour is dropped and counted, then 3 packets, then the median of at least 500 m. Put this in the ADR 0003 amendment word for word.
>
> 2. Q6 side effect, jittering full-precision nodes: do not keep it. Add a rule in PR #58 that two positions of the same node count as the same place when they lie within a fixed floor. Propose the value (I expect about 20 m) with its source and its effect in dB at 500 m, make it a named constant, say where a node inside the floor is placed, give it a named test and a fixture case, and add it to the ADR amendment. Bring me the value and placement in your report; no real-data numbers.
>
> 3. Q6 side effect, finer box mid-hour: do not keep it. A stationary node uses its finest box over the whole hour. Named test and fixture case, and add it to the ADR amendment.

The Q9 (a) text as the lead wrote it on issue #55 (comment 6959488227), verbatim:

> (a) **Recommend.** Only the receipts the hour counts: those that pass `lnk_01`, have a position for each end (`lnk_02`), and pass `lnk_06` and `lnk_05`'s per-receipt test. This matches your words on issue #44, "the box its counted receipts used". The order:
>   1. the per-receipt rules;
>   2. one box per end over the counted receipts, or the hour is dropped and counted;
>   3. `lnk_04`'s 3 packets;
>   4. the median of at least 500 m.

The floor, `STATIONARY_FLOOR_M` (`lnk_02`):
- Why: under the overlap rule, two cells that are not nested never overlap, whatever their size. A node that does not move but reports jittering fine fixes would count as moving, use the 30-minute fallback, and lose most of its link-hours under Q6.
- Where it acts: at 40 m it first matters at 20 bits for two cells of one precision (a 20-bit cell is about 35.7 m wide east to west at 38.63 N), and at 19 bits for a cell beside a finer fix (a 19-bit half-width is about 35.7 m). At 20 m those are 21 and 20 bits. Public-broker positions (10 to 16 bits) are never affected.
- What it measures: every two positions of the node over the 7-day window must lie within the floor, so the floor bounds the diameter of the node's whole cloud of fixes, not the gap between two fixes. One stray fix beyond it marks the node as moved for as long as that fix stays in the window.
- Pairwise, never chained (Casey, issue #55 comment 6960283869, item 1): with fixes A, B and C, A to B 15 m, B to C 15 m and A to C 30 m, the node is moved under a 20 m floor, because A and C are not the same place, even though each is the same place as B. The fixture builds the case from the constant, at 0.75, 0.75 and 1.5 times `STATIONARY_FLOOR_M` (30, 30 and 60 m at 40 m; Casey's 15, 15 and 30 m at a 20 m floor), so it gives moved at whatever floor is chosen.
- Source: the u-blox NEO-6M (datasheet GPS.G6-HW-09005) and NEO-M8N (UBX-15031086) give 2.5 m horizontal accuracy as a circular error probable (CEP, the radius holding half of all fixes), measured over 24 hours static at a strong signal. Other boards use other modules (L76K, UC6580, MAX-M10S). Fixes near buildings or indoors, where many nodes sit, are several times worse. If fixes scatter about their true point as a circular normal (bell-shaped) scatter, the widest fix of a few hundred lies about 2.5 to 3 CEP from the centre, so the cloud's diameter is about 5 to 6 CEP: 12.5 to 15 m at 2.5 m CEP, and 37.5 to 45 m at 7.5 m CEP. These are estimates from that assumption, not measurements.
- Value: 40 m, inclusive (Casey, issue #55, D4). 20 m covers clean open-sky fixes but not the degraded fixes common near buildings; 40 m covers a CEP up to about 7 to 8 m, about three times the open-sky figure. The estimate assumes a bell-shaped scatter; real fixes have heavier tails. A node with an outlier fix beyond the floor is not stationary for as long as that fix stays in its window: it uses the 30-minute fallback, and most of its hours are dropped under Q6 and Q9. An hour whose counted receipts all take one fix is kept and placed at that fix, which can be the outlier itself (Casey accepted this qualification, issue #55, 2026-11-17, item 2).
- Effect at 500 m: the node's box covers every merged fix, so the placed centre lies within the floor of each of them. An error of the floor at one end changes free-space loss by at most 20 log10(500 / 460) = 0.72 dB at 40 m (0.35 dB at 20 m); at both ends, 20 log10(500 / 420) = 1.51 dB at 40 m (0.72 dB at 20 m). These are worst cases, and under D3' the endpoint draws cover the spread of the merged fixes rather than a single point. Both are small beside the 10 to 23 dB RMSE (root-mean-square error) published for ITM (ADR 0002).
- The box around merged fixes (Casey, issue #55, item 2 and D3'): when the floor makes fixes the same place, the node's box is the smallest latitude-longitude rectangle that covers the cells of those positions that hold no finer one of them, centred on the rectangle's midpoint. A cell holds a position when the position's stored value lies in it, low <= value < low + width on both axes, which matches the firmware's truncation (contract `lnk_02`; Casey, issue #44 comment 6960753910, item 3). A 32-bit fix exactly on a coarser cell's lower edge is held by that cell, so the cell adds nothing. A fix exactly on its upper edge is held by the next cell and only touches this one, so the two do not overlap, which agrees with "touching is not overlap". Only the positions the rule reads at that receipt count. Where a box is such a rectangle, the overlap test, the centre and the one-box test read its edges (contract `lnk_02`). A coarse box around fine fixes adds nothing, so a 16-bit box holding two 32-bit fixes gives a box a few metres across, not the 16-bit box. `position_uncertainty_m` and the endpoint draws use this box, so they cover the spread of the merged fixes rather than the finest cell's width. When every two positions overlap, the rule gives the smallest box, as before. This replaces the lower-median placement proposed earlier (D3), which placed tied fixes at a point and so could not cover their spread.
- Tests: `assert_lnk_02_stationary_floor` (two 32-bit fixes 8 m apart: stationary; the nearest grid pairs on each side of the floor, since no two grid points lie exactly on it); `assert_lnk_02_no_chaining` (A, B and C as above, scaled to the floor: moved); `assert_lnk_02_floor_box` (two 32-bit fixes 8 m apart inside one 16-bit box: the box is the rectangle around the two fixes, and `position_uncertainty_m` reflects it; and two 32-bit fixes exactly on a 16-bit cell's lower edge and exactly on its upper edge: the first is held by the cell, the second is not and does not overlap it).

One box per end for the hour (Casey, issue #55, D1 and D2; `lnk_02`, `lnk_04`): each end's box is chosen before `lnk_05`, from the receipts that pass `lnk_01`, `lnk_03` and `lnk_06` and have a position for both ends. An end that is stationary at every one of them, and whose positions over the union of their windows (from 7 days before the first receipt to the last) are pairwise the same place, gets its box for the hour from that union, and every receipt uses it. Otherwise each receipt uses its own box, and Q9 drops the hour if that gives more than one box. The hour's box can use positions from receipts that later fail `lnk_05`, because a position report is evidence of where the node was; Q9 still decides the one-box test on counted receipts only. These differ on purpose (Casey, issue #55, D1).
- Look-ahead: the union can hold a position sent later in the same hour than a receipt, so a stationary node's placement can use a position report sent up to one hour after a receipt. The 30-minute fallback already reads up to 30 minutes after a receipt. Both read position reports only, never signal values (Casey, item 5).
- Which box `lnk_05` uses (Casey, issue #55, item 3 and D9): the per-receipt 500 m to 60 000 m test and the median both use the box each receipt uses, which for an end stationary all hour is the hour's box. A receipt can therefore pass 500 m on its own earlier box and fail on the hour's finer box; it then fails `lnk_05` and does not count.
- Tests: `assert_lnk_02_finest_box_in_hour` (a stationary node whose 20-bit box arrives between a link-hour's second and third receipts: the hour is kept, at the 20-bit box for all of them); `assert_lnk_05_hour_box` (a receipt 510 m away on its own 16-bit box and 490 m away on the hour's 20-bit box: it fails `lnk_05` and is not counted).

Cells (Casey, issue #44 comment 6960753910, item 3, verbatim: "define it to match the firmware's truncation. A cell holds a value when low <= value < low + width in the stored integer units, so every fix is held by exactly one cell at each precision."): the firmware's `truncateCoordinate` masks the 32-bit integer with `UINT32_MAX << (32 - precision)`, which rounds toward minus infinity on the two's-complement pattern and gives the cell's low edge, then adds `1 << (31 - precision)`, half the width, for any precision under 32 (meshtastic/firmware 647831b, `src/mesh/PositionPrecision.cpp`; MapReport does the same in `src/mqtt/MQTT.cpp`, lines 893 to 896). For a negative value the mask still rounds down: -5 at width 4 gives low -8, stored -6 and the cell [-8, -4). So the cell is the half-open interval [low, low + width), low being the stored value minus half the width for 1 to 31 bits and the stored value itself at 32 bits, where the firmware sends the raw value. Cells that only touch do not overlap, as before. When both cells are 1 to 31 bits this is the same test as the centred one above. A 32-bit cell now runs [value, value + 1) instead of being centred on the value, a shift of half a unit (about 0.5 cm): its centre moves from the value to value + 0.5, and one verdict changes. A 32-bit fix exactly on a coarser cell's upper edge overlapped that cell by half a unit under the centred test, and now only touches it. A stored value off the firmware's grid, from a client that does not snap, still gets a defined cell, but two such cells of one precision could partly overlap.

Casey's answers to the D batch (issue #55 comment 6961809084), verbatim, items 1 to 6:

> 1. D1: agree. Add one sentence to the ADR: the hour's box can use positions from receipts that later fail lnk_05, because a position report is evidence of where the node was; Q9 still decides the one-box test on counted receipts only. These differ on purpose.
> 2. D2: agree.
> 3. D3': agree.
> 4. D4: agree, 40 m inclusive. In the ADR, state that the estimate assumes a bell-shaped scatter, that real fixes have heavier tails, and that a node with an outlier fix fails safe (moved, hour dropped). State that the dB figures are worst case and the draws cover the spread under D3'.
> 5. D9: agree.
> 6. D10: agree, write "exactly when".

The D batch itself is issue #55 comment 6961441051.

Box columns (Casey, issue #55 comment 6959184944, verbatim): "Box columns: agree, including the WGS84 metre formula and position_uncertainty_m from the larger half-widths until the rectangle unit." `pos_01` keeps each position's cell as the firmware sent it: `latitude_i`, `longitude_i`, `precision_bits`, the exact cell width `cell_width_i`, and two metre half-widths by a named WGS84 formula; a stored `precision_bits` outside 0 to 32 is `unusable` (Casey, issue #55, D6). `int_link_window` carries, for each end, the box it used under `lnk_02` as exact edges, which hold either a cell or the rectangle around merged fixes, with its precise flag decided only by the positions `lnk_02` read for that end in that hour (Casey, issue #55, D7). `lnk_05`'s `position_uncertainty_m` sums the larger of each end's two half-widths until the rectangle-draws unit. Casey approved the matching change to the warehouse-eng agent file (issue #55 comment 6959592431, item 5).

Conformance-fixture answers (Casey, PR #66 comment 6968763406, items 2 and 4, verbatim): "Q1: (c), provided the flag reads exactly the positions lnk_02 read for that end, look-back included, and nothing more (D7). Q2: yes for the 24-hour and 7-day look-back windows. The clock hour that assigns a receipt to a link-hour stays half-open: a receipt exactly on the hour belongs to the later hour only. Add a case that pins this. Q3: yes. Q4: (a), drop the receipt, and count these drops as a data-quality count in the run report. Q5: yes." and "lnk_07: pin the point order (first point relayer box centre, second gateway box centre) in B3's test." The questions are in PR #66 comment 6967160985. Q1: for a receipt on the 30-minute fallback, the positions it read, which decide the precise flag, are those in its 30-minute window and in its 7-day look-back, and nothing more. Q2: both edges of `lnk_01`'s 24 hours and `lnk_02`'s 7 days count. Q3: `pos_02` tests a 32-bit position's stored value. Q4: a direct hop that the gateway's NeighborInfo resolves to a different node is dropped. Q5: NeighborInfo's "exactly one neighbour" counts across all of the gateway's reports in the 24 hours. `lnk_01`, `lnk_02`, `lnk_04`, `lnk_07`, `pos_02` and PLAN's `precise_source` carry these answers.

Pair boxes, per-pair rows and the MeshSTL flag (Casey, issue #55 comment 6985802330, verbatim; the questions are in PR #70 comments 6984891219 and 6985662273 and PR #69 comment 6984975748):

> 2. Which receipts set a pair's box: (b), only receipts in kept link-hours. Reason: only the receipts the validation uses must agree on position, the same principle as Q9 (a), and pairs are scarce. To keep "reported to me": a pair whose non-kept hours show another box is kept but reported to me in the session, never on issue #44. Add a hand-worked test case that tells (a) from (b), and record this in ADR 0003 as a refinement of my issue #44 wording.
> 3. Counts check order: (a), counts check first, then drop two-box pairs, as for straddles.
> 6. Straddle with per-pair rows: "only its own pair is dropped" means only the pair whose row straddles the tile edge or fails the edge check. That node's other pairs, whose rows lie inside, stay. For the 2026-11-08 counts check, a node with any row touching the tile still counts as touching. Add a test with one node, two pair rows, one inside and one straddling.
> 7. Which copy sets the MeshSTL flags: (a), any copy in any snapshot. Include a test where a later snapshot's MeshSTL copy updates a row core already keeps.
> 8. MeshSTL Position with 0 precision bits: (a), precise_source true when seen_meshstl is true. pos_04 change with its ADR note, in the next contract PR, which comes to me for merge approval.

This refines Casey's issue #44 wording, "gives each pair the box its counted receipts used, so a moving node is placed per pair, and a pair whose receipts used more than one box is dropped and reported to me" (relayed on issue #55 comment 6958831010). A pair's box comes from the receipts of its kept link-hours only. A pair whose kept hours used more than one box at an end is dropped and reported to Casey. A pair whose non-kept hours show another box is kept and reported to him in the session, never on issue #44. A moving node is placed per pair, as his issue #44 wording says and his item 5 confirms ("Per-pair placement sent back without asking: correct, it follows my rule."). At the tile edge each pair is judged by its own row. A node with any row touching the tile counts as touching for the 2026-11-08 counts check, and that check runs before pairs are dropped, for two boxes as for straddles. `pos_04` carries items 7 and 8. Item 8 as Casey later clarified it (Casey, issue #55 comment 6989099568, item 2, verbatim): "my item 8 is about privacy, not the message type. Any 0-bit MeshSTL position that carries coordinates, Position or MapReport, gets precise_source true when seen_meshstl is true." The decoder stores a MapReport's coordinates whenever either is non-zero, with `precision_bits` from its `position_precision` (`ingester/decoder.py`, `_map_report`), so a 0-bit MapReport can carry coordinates.

A broadcast's gateway copies (Casey, issue #55 comment 6995037213, item 3, verbatim): "Q3: (a). A broadcast's gateway copies count as one position, timed by the first copy, precise if any copy qualifies. Record the late-window-edge effect in the ADR." The question is in PR #72. The copies of one position broadcast heard at several gateways arrive seconds apart, and the link rules read them as one position at the time of the first copy, precise when any copy qualifies under `pos_04`. When the copies straddle the edge of a window the rules read (the 7-day look-back, the 30-minute fallback, or the union an hour box is chosen from), the whole broadcast counts as inside or outside by its first copy. At a window's early edge, a first copy just outside means no copy is read, even one just inside (staging case s12). At a window's late edge, a first copy just inside means the broadcast is read and counts as precise even when its only qualifying copy arrived just outside. The late edge is the receipt's own instant for a stationary end's look-back, 30 minutes after the receipt for the fallback, and the hour's last receipt for the hour box (PR #72).
