# Spike results for ADR 0003

Tables printed by `analyze.py` (commands and definitions in `README.md`). Interim until both captures end; every weekly figure is extrapolated from the hours observed.

## Interim results, public broker (4.38 hours; weekly figures extrapolated x38.32)

(a) Envelopes by via_mqtt:

| measure | radio deliveries | gateway's own excluded | via_mqtt true |
| --- | --- | --- | --- |
| envelopes | 315 829 | 188 431 | 6 288 685 |
| header-only (status stripped) | 32.5% | 48.7% | 100.0% |
| `relay_node` set | 77.7% | 85.5% | 0.02% |
| `relay_node` set, among header-only | 84.1% | 94.1% | 0.02% |
| own low byte, among relayed | 52.8% | 28.3% | 0.4% |

Decode status, all envelopes: stripped 96.1%, ok 2.7%, decrypt_failed 0.7%, malformed 0.5%. On radio deliveries: ok 51.3%, stripped 32.5%, wrong key 8.9%, PKI 6.6%. Distinct radio receipts: 37 672, of which 32 051 carry `relay_node`; those resolve to one sender heard by the same gateway in 59.7%, several in 9.7%, none in 30.6%.

Decoded portnums (share of decoded): Position 39.4%, NodeInfo 19.4%, Telemetry 15.8%, MapReport 8.2%, Routing 7.7%, Text 5.2%, Traceroute 4.2%. Seven portnums in all, none unknown, NeighborInfo 0. Position packets at 20 bits or more: 26, from 19 nodes nationally.

Funnel, distinct radio receipts, combined rule:

| step | MSA | MO plus IL |
| --- | --- | --- |
| radio receipts with `relay_node` at area gateways | 316 | 864 |
| `lnk_06` fail: signal missing or out of range | 1 | 5 |
| `lnk_01` fail: relayer not resolved | 76 | 298 |
| `lnk_01` and `lnk_03` pass | 239 | 561 |
| `lnk_02` fail: relayer, no decodable position in the capture | 157 | 334 |
| `lnk_02` fail: relayer, below 15 bits | 38 | 155 |
| `lnk_02` fail: relayer, position only outside the area | 0 | 6 |
| `lnk_02` fail: relayer, usable but outside the window | 7 | 8 |
| `lnk_02` fail: gateway, below 15 bits | 12 | 27 |
| `lnk_02` fail: gateway, usable but outside the window | 4 | 5 |
| `lnk_02` pass | 21 | 26 |
| `lnk_05`: boxes overlap, distance 0 m to the box diagonal | 20 | 25 |
| `lnk_05` pass | 1 | 1 |
| link-hours with 3 or more receipts | 0 | 0 |

Relayers that failed `lnk_02` with no decodable position in the capture so far: 21 in the MSA (19 sent other decoded packets), 46 in MO plus IL (44 did). Overlap diagonals: MSA median 926 m, max 1 852 m.

(2a) Time to first usable position, relayers resolved in the MSA (35) and all senders heard by radio (8 744):

| measure | MSA relayers | all radio senders |
| --- | --- | --- |
| no decodable position in the capture | 21 (60.0%) | 5 744 (65.7%) |
| positions only below 15 bits | 7 (20.0%) | 2 154 (24.6%) |
| reached a usable position | 7 (20.0%) | 846 (9.7%) |
| hours to first usable: p50, p75, p90, max | 0.0, 0.8, 3.6, 3.6 | 0.0, 0.4, 1.6, 4.2 |
| histogram 0 to 1, 1 to 2, 2 to 4, 4 to 8 h | 6, 0, 1, 0 | 720, 65, 59, 2 |

MO plus IL relayers (82): 46 with no decodable position (56.1%), 25 only below 15 bits, 11 usable.

Position rule options, combined link rule, link-hours per week (relayers, gateways, pairs):

| position rule | MSA 500 m | MSA 3 km | MO plus IL 500 m | MO plus IL 3 km |
| --- | --- | --- | --- | --- |
| keep 30 minutes | 0 (0, 0, 0) | 0 (0, 0, 0) | 0 (0, 0, 0) | 0 (0, 0, 0) |
| stationarity window | 0 (0, 0, 0) | 0 (0, 0, 0) | 0 (0, 0, 0) | 0 (0, 0, 0) |
| gateway-only fallback | 575 (8, 6, 8) | n/a | 1 035 (13, 11, 13) | n/a |

Stationary usable nodes: 24 of 32 in the MSA, 55 of 83 in MO plus IL.

Group growth, cumulative, `lnk_02` and `lnk_05` ignored (the contract rows are 0 at every hour):

| hours | MSA link-hours, relayers, gateways, pairs | MO plus IL |
| --- | --- | --- |
| 4 | 19, 8, 7, 9 | 45, 20, 19, 22 |
| 8 to 48 | 24, 11, 9, 12 (capture is 4.4 h long) | 54, 24, 21, 26 |

Sensitivity, combined rule, MSA, per week at 500 m (not the contract rules): 30 minutes with 3 packets 0; with 1 packet 38 (1 pair); 24 hours with 3 packets 0; with 1 packet 115 (1 pair).

Other observations: of the packets that keep a payload, 75.5% carry the OK to MQTT bit and 24.5% do not, so the bit alone does not explain which radio deliveries arrive header-only. MapReport packets carry `position_precision` up to 32 bits (8.9% at 32) and count as positions at their own precision. The regional topic segments carry radio deliveries only.

## MeshSTL results

Pending; the capture has not started. The same tables will be printed here from `meshstl-capture`, plus the portnum shares, NeighborInfo (packets, pairs, median SNR), the precision_bits distribution and the count of positions at 20 bits or more, with the public broker restricted to the same hours.
