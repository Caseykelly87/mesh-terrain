# Link-rules conformance fixture

A small synthetic set of receipts and positions with the expected results written by hand, so that the two implementations of the link rules can be checked against the same answers (Casey, issue #55, meeting point 1). Both must pass it:

- warehouse-eng's SQL: `stg_packet`, `stg_position`, `core.fct_node_position`, `core.fct_rx_observation` and `core.int_link_window` (B3 and the units after it);
- ingest-eng's Python: the unit that moves the link-hour rules `engine_inputs` uses out of the ADR 0003 spike script (Casey's item 4).

Changing the fixture needs review by both ingest-eng and warehouse-eng, and the PR names the contract rule each changed case follows. A disagreement about what a rule means goes to Casey as a question, not a fixture edit (Casey, issue #55 comment 6963192892, item 3).

## How the expected results were worked out

Every outcome was decided by hand from the contract on main (`docs/data-quality-contract.md` at 8370dbd) and ADR 0003's Overlap and Link-rule amendments, with the reasoning written in each case's `reasoning` list. c44 to c51 were decided from Casey's answers to the PR #66 questions (PR #66 comment 6968763406, items 2 and 4) as PR #67 words them in the contract and PLAN (at 5e43c17), and each quotes that wording in its first reasoning line; c02 and c43 quote it where it makes a receipt's own sender a candidate for that receipt, which changes none of their outcomes. c52 to c55 were decided from ADR 0003's paragraph on a broadcast's gateway copies (Casey, issue #55 comment 6995037213, item 3, as ADR 0003 records it at bfe51aa), and each quotes it in its first reasoning line. c56 was decided from Casey's answer to PR #79's Q1 (issue #55 comment 7016583529, item 1), which it quotes in its first reasoning line. No expected value came from running B3, the ingester or the spike. Arithmetic used these named calls and formulas, so anyone can recompute it:

- Firmware grid: for 1 to 31 bits the stored value is the cell's low edge plus half its width, the low edge being the value rounded down to a multiple of the width `2^(32 - precision_bits)` (Python `//`, which rounds toward minus infinity as the firmware's mask does on negative values). At 32 bits the stored value is the raw value.
- Cell edges: `low = stored - width / 2` (1 to 31 bits) or `low = stored` (32 bits); the cell is `[low, low + width)` on each axis.
- Distances: `pyproj.Geod(ellps="WGS84").inv` between box centres, a centre being the midpoint of the box's edges in degrees (`edge * 1e-7`). PostGIS `ST_Distance` on `::geography` with the spheroid computes the same geodesic. The 40 m floor uses the same call between stored coordinates.
- Half-widths: `pos_01`'s WGS84 formula, at the stored latitude for a single cell and at the box midpoint's latitude for a merged rectangle, from `(north_i - south_i) / 2` and `(east_i - west_i) / 2` (PLAN, Data model).
- Medians: `percentile_cont(0.5)`, which averages the two middle values of an even count (`lnk_04`).
- Inside the MSA: `shapely` `contains` on the polygon joined from `dbt/seeds/msa_boundary.csv`.
- Distance to the MSA boundary (c29's reasoning only): the shortest `pyproj.Geod(ellps="WGS84").inv` distance from the fix to a point on the polygon's edges, each edge a straight line in degrees, searched along its length. The nearest point in degrees is not the geodesic nearest point.
- Side of the MSA boundary at millimetre range (c50): the sign of the cross product of the boundary edge and the point, in exact rational arithmetic (Python `fractions.Fraction`) on the seed's decimal vertices and the stored integers, checked against `shapely` `contains`. The millimetre figures in c50's reasoning describe the margin only.

## The order the cases follow

This is the contract's order (`lnk_04`, Q9 (a), D1) written as steps; the cases cite the step numbers.

1. `ing_06`: of several copies of one packet (origin, `packet_id`) at one gateway, keep the first by `received_at`. `lnk_01`: a receipt has `via_mqtt` false, is not the gateway's own packet, and has a non-null `relay_node`. A low byte of 0 is read as 0xFF. `relay_node` equal to the sender's own byte is a direct hop. Otherwise the candidates are the senders of every radio delivery (`via_mqtt` false, any decode status) the gateway heard in the 24 hours before `received_at`, its own packets included; NeighborInfo is the gateway's own reports in the same 24 hours, and "exactly one neighbour" counts across all of them together (c49). Both edges of the 24 hours count: a delivery exactly 24 hours before, and one at the receipt's own instant, the receipt itself included (c45, c51). When relay_node and NeighborInfo resolve to different nodes the receipt is dropped, a direct hop included (c06, c48).
2. `lnk_03`: relayer and gateway differ.
3. `lnk_06`: RSSI in [-150, 0) dBm and SNR in [-30, 20] dB, both present; NaN is missing.
4. Positions a node reads (`lnk_02` with `pos_01` to `pos_03`): `precision_bits` 15 to 32, both coordinates present, not 0, 0, inside the MSA at the stored coordinates (for 32 bits the stored value, not the cell centre; c50); either broker, either source.
5. Each end at each receipt: stationary when it has at least one position in the 7 days before (inclusive at both edges, so a position at the receipt's own instant is read; c30, c46) and every two are the same place (cells overlap, half-open, or stored coordinates within 40 m inclusive; pairwise, never chained); its box is the rectangle around the cells that hold no finer position. Otherwise the position nearest in time within 30 minutes either side (inclusive, the earlier on a tie), its cell being the box. Otherwise no position: dropped by `lnk_02`.
6. Each end's box for the hour, over the hour's receipts that passed steps 1 to 5: when the end is stationary at every one and every two positions from 7 days before the first to the last are the same place, the box from that union; otherwise each receipt keeps its own.
7. `lnk_05` per receipt, with the box each receipt uses: distance 500 to 60 000 m inclusive, and the two boxes do not overlap.
8. One box per end over the receipts that passed step 7 (the counted receipts), all four edges equal; otherwise the hour is dropped by `lnk_02` and counted in `mart_data_quality`.
9. `lnk_04`: at least 3 counted receipts.
10. `lnk_05`: median distance at least 500 m.

The hour is `received_at` truncated to the hour in UTC, half-open, so a receipt exactly on the hour belongs to the later hour only (`lnk_04`; c47). The link is (relayer, gateway, hour) (`lnk_08`).

An end on the 30-minute fallback has its precise flag read from the positions in each counted receipt's 30-minute window and 7-day look-back, and nothing more (PLAN, Data model; c44).

A position broadcast heard at several gateways is several deliveries and one position: its copies share node, `packet_id` and stored values, it is timed by the first copy's `received_at`, and it qualifies under `pos_04` when any copy does (ADR 0003: "A broadcast's gateway copies count as one position, timed by the first copy, precise if any copy qualifies."). Steps 5 and 6 place it in or out of each window by that time alone, so a copy outside a window is read when the first copy is inside, and no copy is read when the first is outside (c52 to c55). When two broadcasts are equally near a receipt and their first copies share an instant, the fallback takes the one whose first copy has the smaller `delivery_key` (Casey, issue #55 comment 7016583529, item 1: "(a), the broadcast whose first copy has the smaller delivery key"; c56).

## Layout

`cases/cNN_<name>.json`, one file per case, with these keys in this order:

- `case_id`, `title`, `rules` (the contract rules the case tests), `reasoning` (the hand-worked steps).
- `messages`: rows shaped like `raw.mqtt_message`. Every row has `delivery_key`, `received_at`, `origin_pseudonym`, `origin_low_byte`, `gateway_pseudonym`, `gateway_low_byte`, `via_mqtt`, `seen_public`, `seen_meshstl`, `packet_id`, `relay_node` and `portnum`. A row may add `topic`, `decode_status`, `rx_time`, `rx_snr`, `rx_rssi`, `position_source`, `latitude_i`, `longitude_i`, `precision_bits`, `neighbour_pseudonyms` and `neighbour_low_bytes`; an absent column is null.
- `boxes`: named boxes with `south_i`, `north_i`, `west_i`, `east_i` (bigint, 1e-7 degrees, half-open), `merged`, `precision_bits`, `half_width_ns_m`, `half_width_ew_m`, as PLAN's data model defines the `int_link_window` box columns. A case holds only the boxes its expected rows name; the reasoning names any other position by its delivery key.
- `expected_receipts`: one row per message with a non-null `relay_node`, plus any other message a case is about. `outcome` is `counted` (passes step 7) or `dropped` with the `rule` that decides it. `relayer` and `link_source` are present once `lnk_01` resolves the relayer. `relayer_box`, `gateway_box` and `distance_m` are present once the receipt reaches step 7, naming the box it uses. A message not listed is never counted.
- `expected_link_hours`: every (relayer, gateway, hour) with at least one counted receipt. `receipts` and `packets` are the counted receipts. `outcome` is `kept` or `dropped` with its `rule`. A kept hour adds `median_snr_db`, `median_rssi_dbm`, `distance_m`, `relayer_box`, `relayer_precise_source`, `gateway_box`, `gateway_precise_source` and `position_uncertainty_m`.

Conventions:

- Pseudonyms are readable (`c23-r`, `c23-g`) and start with their case number, so no two cases share a node and every case can be loaded at once into one table. Times are UTC with a `Z`.
- Position rows are the node's own packets with `relay_node` null. Most are uplinked by the node itself (`gateway_pseudonym` is the node); the copies of a broadcast in c52 to c55 are uplinked by gateways named `-h1` and `-h2`, which have no receipts. No position row makes a node a candidate at a gateway that has receipts. A node heard only to make it a candidate has a row with `relay_node` null (portnum 67).
- `rx_snr` `"NaN"` is the IEEE value NaN.
- Portnums: 1 text, 3 Position, 67 telemetry, 71 NeighborInfo, 73 MapReport. A MapReport row carries the `map` topic raw lands it on, `msh/US/2/map/` (`ingester/topics.py`).

## Loading and comparing

Both sides fill raw's other not-null columns the same way: `snapshot_id` `link-rules-fixture` (one `raw.snapshot` row), `topic` `msh/US/2/e/LongFast/<gateway_pseudonym>` when absent, `decode_status` `ok` when absent, `hop_limit` 0, `payload_length` 0, `decoder_version` `fixture`, `schema_version` 1.

- SQL: insert every message into `raw_dev_wh.mqtt_message` on the `dev_wh` target, run `dbt build`, and compare `core.fct_rx_observation` and `core.int_link_window` (through views or singular tests) with the expected rows.
- Python: read the case files, build the same rows, run the rules, and compare.

Comparison: the counted set and each dropped receipt's rule exactly; relayer and `link_source` exactly; boxes by all four edges, `merged` and `precision_bits` exactly; distances within 1e-4 m (the boundary cases in c34 and c35 are 0.25 mm to 11 mm from their limit, and both libraries compute the same geodesic); half-widths and `position_uncertainty_m` within 1e-6 m; medians exactly; the precise flags exactly. Across the fixture two hours are dropped by step 8 (c24 on the relayer side, c42 on the gateway side), so `mart_data_quality` counts 2.

Direct hops contradicted by NeighborInfo (`lnk_01`; Casey's Q4, "a data-quality count in the run report"): the count is named `lnk_01_direct_hop_contradicted` and counts receipts. It is the receipts dropped by `lnk_01` whose `relay_node` equals the sender's own low byte while the gateway's NeighborInfo resolves that byte to another node; a relayed receipt whose two methods disagree (c06's 10:20 receipt) is not in it. Across the fixture it is 3, all in c48. Where each implementation keeps the count is not set here: the units that build it bring that to Casey.

Geometry (`lnk_07`, SQL only, since the Python side builds none): each kept hour's link geometry is valid and is a two-point LINESTRING whose first point is the centre of its relayer box and second the centre of its gateway box (`lnk_07`: "its first point is the relayer box centre and its second the gateway box centre"; Casey, PR #66 comment 6968763406, item 4), x being the centre's longitude and y its latitude (SRID 4326), each coordinate within 1e-9 degrees, a centre being the midpoint of the box's edges times 1e-7. The point order is compared.

## Cases

| Case | What it tests | Rules |
| --- | --- | --- |
| `c01_direct_hop_kept` | direct hop, both ends stationary, every kept-hour output | lnk_01, lnk_02, lnk_04, lnk_05, lnk_07, lnk_08 |
| `c02_relayed_one_candidate` | one candidate for the relay byte | lnk_01 |
| `c03_two_candidates_share_the_byte` | two candidates, no NeighborInfo | lnk_01 |
| `c04_candidate_heard_25_hours_before` | candidate window, 23 h in, 25 h out | lnk_01, lnk_04 |
| `c05_not_radio_receipts` | via MQTT, the gateway's own packet, null relay_node | lnk_01 |
| `c06_neighbor_info_resolution` | NeighborInfo resolves, agrees, contradicts, fails | lnk_01, lnk_04 |
| `c07_low_byte_zero` | low byte 0 read as 0xFF, direct and relayed | lnk_01 |
| `c08_candidates_via_mqtt_or_header_only` | MQTT-only sender is no candidate, header-only radio sender is | lnk_01, lnk_04 |
| `c09_neighbor_info_too_old_or_not_the_gateways` | NeighborInfo 25 h old, or another node's | lnk_01 |
| `c10_gateway_resolves_as_its_own_relayer` | relayer resolves to the gateway | lnk_01, lnk_03 |
| `c11_stationary_repeated_cell` | one cell sent three times | lnk_02 |
| `c12_nested_15_and_20_bits` | nested cells, placed at the finer | lnk_02 |
| `c13_mapreport_and_position_on_two_brokers` | either broker, either source; precise flag | lnk_02, pos_04 |
| `c14_touching_cells_moved` | cells that only touch | lnk_02 |
| `c15_other_broker_cell_does_not_overlap` | a non-overlapping cell on the other broker | lnk_02 |
| `c16_floor_box_8_m` | two 32-bit fixes 8 m apart: merged rectangle | lnk_02, lnk_05, pos_04 |
| `c17_fix_on_a_cell_edge_half_open` | a fix on a cell's lower edge and on its upper edge | lnk_02 |
| `c18_floor_boundary_nearest_grid_pairs` | 39.996 m and 40.007 m | lnk_02, pos_04 |
| `c19_no_chaining` | A, B, C at 30, 30 and 60 m | lnk_02 |
| `c20_outlier_hour_takes_one_fallback_fix` | outlier node kept at one fallback fix | lnk_02 |
| `c21_finer_box_arrives_mid_hour` | finer cell mid-hour: the whole hour uses it | lnk_02 |
| `c22_hour_box_moves_a_receipt_under_500_m` | 510 m on its own box, 490 m on the hour's | lnk_02, lnk_05 |
| `c23_q9_400_600_600_600` | Q9: the 400 m receipt fails first, hour kept | lnk_02, lnk_04, lnk_05 |
| `c24_q6_two_fallback_boxes_drop_the_hour` | two boxes over the counted receipts | lnk_02 |
| `c25_fallback_picks_nearest_in_time` | 29 and 31 minutes, nearest, exactly 30, a tie | lnk_02 |
| `c26_fallback_from_a_position_after_the_receipt` | nothing before, 20 minutes after, 2 hours after | lnk_02 |
| `c27_coarse_cell_not_read` | a 14-bit cell | pos_01, lnk_02 |
| `c28_outside_the_msa_not_read` | cells outside the MSA | pos_02, lnk_02 |
| `c29_msa_edge_just_inside_and_just_outside` | fixes about 28.7 m either side of the boundary | pos_02, lnk_02 |
| `c30_seven_day_boundary` | exactly 7 days, and 7 days and 1 minute | lnk_02 |
| `c31_position_at_zero_or_missing_a_coordinate` | 0, 0 and a missing latitude | pos_01, pos_03, lnk_02 |
| `c32_precision_bits_above_32_or_negative` | 33 and -1 bits | pos_01, lnk_02 |
| `c33_three_packets_and_hour_by_received_at` | 2 and 3 packets; rx_time across the hour | lnk_04, ing_06 |
| `c34_distance_500_m_boundary` | 499.989 m and 500.0003 m | lnk_05 |
| `c35_distance_60_km_boundary` | 59 999.992 m and 60 000.0006 m | lnk_05 |
| `c36_relayer_box_inside_gateway_box` | overlapping boxes 600 m apart fail | lnk_05 |
| `c37_signal_ranges` | RSSI and SNR limits, missing, 0, NaN | lnk_06 |
| `c38_median_of_an_even_count` | four receipts | lnk_04 |
| `c39_one_row_per_relayer_gateway_hour` | three rows | lnk_08 |
| `c40_both_brokers_and_two_copies` | a delivery both brokers carried; the first copy kept | ing_01, ing_06 |
| `c41_precise_flag_from_positions_read_in_the_hour` | D7: the flag reads the hour's positions only | pos_04, lnk_02 |
| `c42_gateway_two_fallback_boxes_drop_the_hour` | c24 on the gateway side: moved gateway, two boxes | lnk_02 |
| `c43_neighbour_low_byte_zero` | NeighborInfo neighbour byte 0 read as 0xFF | lnk_01 |
| `c44_fallback_precise_flag_reads_the_look_back` | Q1: a fallback end's flag reads the 7-day look-back, its edge, and nothing later | pos_04, lnk_02 |
| `c45_candidate_and_neighbor_info_window_edges` | Q2: exactly 24 hours, the receipt's own instant, 24 hours and 1 second | lnk_01 |
| `c46_position_at_the_receipts_own_instant` | Q2: a position at the receipt's instant is in the 7-day look-back | lnk_02 |
| `c47_receipt_on_the_hour_belongs_to_the_later_hour` | the half-open clock hour | lnk_04, lnk_08 |
| `c48_direct_hop_contradicted_by_neighbor_info` | Q4: contradicted, confirmed and unresolved direct hops | lnk_01 |
| `c49_neighbor_info_reports_counted_together` | Q5: two reports, two nodes with one byte; one node in both | lnk_01 |
| `c50_32_bit_stored_value_at_the_msa_edge` | Q3: stored value inside, centre outside, and the reverse | pos_02, lnk_02 |
| `c51_neighbor_info_at_the_receipts_own_instant` | Q2: a NeighborInfo report at the receipt's instant | lnk_01 |
| `c52_broadcast_copies_at_the_look_back_late_edge` | broadcast copies at a stationary end's look-back late edge: first at the receipt's instant, qualifying copy 1 s later | pos_04, lnk_02 |
| `c53_broadcast_copies_at_the_fallback_late_edge` | broadcast copies at the fallback's late edge: first exactly 30 minutes after, qualifying copy outside | pos_04, lnk_02 |
| `c54_broadcast_copies_at_the_hour_box_late_edge` | a finer broadcast at the hour box union's late edge sets the box; qualifying copy 1 s later | pos_04, lnk_02 |
| `c55_broadcast_copies_at_the_fallback_early_edge` | broadcast copies at the fallback's early edge: first 10 s outside, not chosen, still read for the flag | pos_04, lnk_02 |
| `c56_fallback_tie_goes_to_the_smaller_first_copy_key` | two broadcasts tied at one instant: the smaller first-copy key wins, not the smallest key over all copies | lnk_02 |

The Rules column lists the main rules; each file's `rules` also names the rules its other expected rows cite.

The named tests in the contract's `lnk_02` and `lnk_05` rows and their cases (ADR 0003, Link-rule amendment, Tests): `assert_lnk_02_stationary_floor`, c16 (8 m) and c18 (the nearest grid pairs on each side of 40 m); `assert_lnk_02_no_chaining`, c19; `assert_lnk_02_floor_box`, c16 (the rectangle and its `position_uncertainty_m`) and c17 (the lower and upper edges); `assert_lnk_02_finest_box_in_hour`, c21; `assert_lnk_05_hour_box`, c22.

Casey's minimum list (issue #55 comment 6963192892, item 4): Q9's 400/600/600/600 m hour, c23; the A-B-C chain, c19; fixes inside and outside the floor, c16 and c18; a fix exactly on a coarser cell's edge, c17; D9's 510 m and 490 m, c22; a finer box mid-hour, c21; `precision_bits` above 32 and negative, c32; D7's precise flag, c41; an outlier node kept at one fallback fix, c20.

The case list in issue #55's body (section 2, 1 to 35) maps as follows: 1 c01; 2 c02; 3 c03; 4 c04; 5 to 7 c05; 8 c06; 9 c07; 10 c08; 11 c09; 12 c11; 13 c12; 14 c13; 15 c14; 16 c18 and c19; 17 c15; 18 c23; 19 c25; 20 c26; 21 c27; 22 c28; 23 c29; 24 c30; 25 c31; 26 c10; 27 c33; 28 c34, c35 and c23; 29 c36; 30 every kept hour, c16 for a merged box; 31 c37; 32 c38; 33 every kept hour; 34 c39; 35 c40. Where the list predates the contract, the contract wins: case 16 ("two different 32-bit readings: moved") now depends on the 40 m floor, and case 28's 400, 600 and 700 m hour is replaced by Q9's 400/600/600/600 m hour, which tests the same order.

Casey's answers to the PR #66 questions (PR #66 comment 6968763406, items 2 and 4) map as follows: Q1 c44 (look-back, its 7-day edge, a copy outside both windows, and a copy later in the hour); Q2 c45 (candidates at exactly 24 hours and at the receipt's instant, NeighborInfo at exactly 24 hours), c51 (NeighborInfo at the receipt's instant), c02 and c43 (a receipt's own sender is a candidate for it), c46 and c44 (the 7-day look-back), and c47 (the clock hour); Q3 c50; Q4 c48; Q5 c49; item 4, `lnk_07`'s point order, every kept hour (Loading and comparing). c26's two receipts share an instant at one gateway; both are direct hops, so Q2 changes nothing there.

Casey's answer to PR #72's Q3 (issue #55 comment 6995037213, item 3), as ADR 0003 records it at bfe51aa, maps as follows: the late edge of a stationary end's 7-day look-back, c52; the fallback's late edge, c53; the late edge of the union an hour box is chosen from, c54; the fallback's early edge, c55 (c55-ra, the broadcast cannot be chosen; c55-rb, it is still read for the precise flag). In c52, c53, c54 and c55-ra, timing the broadcast by a later copy, or reading each copy as its own position, gives a different expected row; each case's reasoning says which. Casey's answer to PR #79's Q1 (issue #55 comment 7016583529, item 1), the tie between two broadcasts at one instant, maps to c56, where the smallest key over all copies would choose the other cell.

## Not covered

- A delivery present in two snapshots (`ing_01`): the fixture is one snapshot, as the Python side reads one; B3's `stg_packet` test covers it.
- The thresholds for impossible readings (`lnk_06`): they wait on warehouse-eng's staging proposal. Every impossible value is outside the ranges anyway, so the outcome is the same.
- Validation's 3 km filter (`lnk_05`, marts) and `link_source` in `int_link_window`.
- On the gateway side, an end with no position and an end on an hour box chosen over a finer cell: c15, c26 and c21 test those steps on the relayer side, and c42 tests a moved gateway on the fallback and the one-box test for the gateway.
- A gateway's own NeighborInfo report uplinked by another gateway: the possible sixth question in PR #66 comment 6967160985, which Casey has not answered. Every report in the fixture is uplinked by the node that sent it.
- A broadcast's copies at the early edge of the 7-day look-back, which is also the early edge of the hour box's union: staging case s12 (PR #72; SQL side only) pins it; no case here does.
- Copies of one packet at a single gateway at a window edge: every broadcast in c52 to c55 reaches two gateways, so the reading `ingester/link_rules.py` states for copies at one gateway is not tested here.
- The gateway side of c52 to c55: each pins a relayer, and the gateway's positions go through the same steps.
- Copies of one broadcast that differ in stored values, which the link rules do not match as one position (ADR 0003): every broadcast in the fixture has copies with equal stored values.
