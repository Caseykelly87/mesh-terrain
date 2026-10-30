# Spike results for ADR 0003

Tables printed by `analyze.py`. The commands, the seven runs behind these tables and the definitions are in `README.md`. Both captures have ended; these numbers are final.

## Denominators

Every rate divides by observed hours: the sum of a capture's segments, where a segment is a run of records with no gap over 15 minutes. Every weekly figure is weekday-weighted: the weekday hourly rate times 120 plus the weekend hourly rate times 48, with days taken in local time (CDT, UTC minus 5, for the whole capture). Where the flat figure (overall hourly rate times 168) differs, it is shown beside it.

| capture | segments | observed h | weekday h | weekend h |
| --- | --- | --- | --- | --- |
| public broker, full | main, 2026-10-22T16:11:36Z to 2026-10-25T07:43:14Z, 63.527 h; fragment 1, 2026-10-28T20:40:58Z to 20:44:58Z, 0.067 h | 63.594 | 36.873 | 26.721 |
| public broker, shared window | 2026-10-23T22:08:59Z to 2026-10-24T22:08:58Z | 24.000 | 6.850 | 17.150 |
| MeshSTL broker | 2026-10-23T22:09:00Z to 2026-10-24T22:08:51Z | 23.998 | 6.850 | 17.148 |

Fragment 1 is the four minutes the public capture wrote after Docker Desktop restarted it on 2026-10-28 (ADR 0003, Consequences). It is inside every public total and labelled in the hourly curve; it adds 0.067 observed hours and 5 metro relayed radio receipts. The wall clock from the first public record to the last is 148.56 hours, and no rate uses it. The MeshSTL capture also ran past its window: its 9 881 records after 2026-10-24T22:08:59Z, its own four-minute fragment on 2026-10-28 included, are outside `--end` and in no table.

## Headline: link-hours in the MSA

Combined link rule. Each cell is link-hours per observed hour, with the weekday-weighted week in brackets.

| position rule | public, 63.59 h | public, shared 24 h | MeshSTL, 24 h | public 24 h with MeshSTL positions | MeshSTL 24 h with public positions |
| --- | --- | --- | --- | --- | --- |
| keep 30 minutes, 500 m | 0.66 (110) | 0.88 (177) | 3.08 (560) | 1.83 (344) | 3.08 (560) |
| keep 30 minutes, 3 km | 0.64 (107) | 0.88 (177) | 1.33 (281) | 0.88 (177) | 1.33 (281) |
| stationarity window, 500 m | 0.97 (168) | 1.21 (228) | 3.71 (617) | 2.21 (399) | 3.71 (617) |
| stationarity window, 3 km | 0.94 (162) | 1.21 (228) | 1.75 (309) | 1.21 (228) | 1.75 (309) |
| gateway-only fallback | 5.03 (852) | 5.33 (962) | 8.33 (1 502) | 5.46 (1 000) | 8.38 (1 505) |
| positions ignored (diagnostic, not a contract rule) | 7.03 (1 194) | 7.08 (1 256) | 13.83 (2 328) | 7.29 (1 299) | 13.83 (2 328) |

Observed link-hours behind the first three columns, with distinct (relayers, gateways, pairs):

| position rule | public, 63.59 h | public, shared 24 h | MeshSTL, 24 h |
| --- | --- | --- | --- |
| keep 30 minutes, 500 m | 42 (2, 4, 4) | 21 (1, 2, 2) | 74 (6, 6, 7) |
| keep 30 minutes, 3 km | 41 (1, 3, 3) | 21 (1, 2, 2) | 32 (4, 4, 5) |
| stationarity window, 500 m | 62 (4, 6, 6) | 29 (2, 2, 3) | 89 (6, 6, 7) |
| stationarity window, 3 km | 60 (3, 5, 5) | 29 (2, 2, 3) | 42 (4, 4, 5) |
| gateway-only fallback | 320 (23, 16, 28) | 128 (18, 10, 19) | 200 (26, 14, 28) |
| positions ignored | 447 (36, 25, 46) | 170 (26, 15, 27) | 332 (39, 22, 43) |

Widening check (f), combined rule at 3 km against 300 link-hours a week: public 107, triggered; MeshSTL 281, triggered (its stationarity row, 309, would not be). The MeshSTL weekday rate rests on 6.85 hours, all Friday evening (17:09 to 24:00 CDT): stationarity at 3 km is 13 link-hours in those hours, 1.90 an hour. Unweighted, the same row is 294 a week, under 300, so whether MeshSTL clears the threshold depends on that one evening. Missouri plus Illinois on the public broker, full capture: keep 30 minutes 116 a week at 500 m and 113 at 3 km, stationarity 364 and 357, gateway-only 1 387. Under the contract rule as written, widening the area adds 6 link-hours a week; under the stationarity rule it adds 196 at 500 m and 195 at 3 km, and Missouri plus Illinois clears 300 where the MSA (162) does not.

Flat against weighted, full public capture: within 2.4 percent in every row (stationarity at 500 m 164 flat against 168 weighted, at 3 km 159 against 162, positions ignored 1 181 against 1 194, gateway-only 845 against 852). The shared window is mostly weekend (17.15 of 24 hours), so weighting raises its figures, for example 147 flat to 177 weighted for keep 30 minutes at 500 m.

Contract-rule link-hours grow with capture length: the public MSA had 0 after 4 observed hours, 13 after 24, 34 after 48 and 42 after 63.59, from at most 2 relayers and 4 gateways. The interim 4.38-hour figure of zero was a short-window artefact, not a property of the broker.

## Weekend effect, hour by hour (public broker, full capture)

Per local day, counts divided by that day's observed hours:

| local day | class | observed h | records | radio deliveries | internet copies | area radio receipts | contract 500 m | gateway-only | positions ignored |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Thu 2026-10-22 | weekday | 12.81 | 1452872.39 | 68139.05 | 1366313.00 | 83.55 | 0.55 | 5.08 | 6.64 |
| Fri 2026-10-23 | weekday | 24.00 | 1296587.17 | 66235.62 | 1184679.38 | 99.62 | 0.71 | 5.21 | 7.62 |
| Sat 2026-10-24 | weekend | 24.00 | 1172736.92 | 67333.38 | 1048827.12 | 85.58 | 0.71 | 4.96 | 6.71 |
| Sun 2026-10-25 | weekend | 2.72 | 1242772.73 | 65900.35 | 1157262.31 | 88.21 | 0.37 | 4.04 | 6.62 |
| Wed 2026-10-28 | weekday | 0.07 | 1304937.92 | 68288.68 | 956580.33 | 74.83 | 0.00 | 0.00 | 0.00 |

Nationally, records per hour fall from 1.45 million on Thursday to 1.17 million on Saturday, and all of the fall is in internet copies (1.37 million to 1.05 million an hour). Radio deliveries hold at 66 000 to 68 000 an hour every day. In the MSA, gateway-only link-hours run 5.08, 5.21 and 4.96 an hour on Thursday, Friday and Saturday; Sunday's 4.04 covers 2.72 overnight hours only. The weekday rate is 5.15 and the weekend rate 4.87, so the weighting moves a week by under 1 percent. No hour of the main segment is empty.

Hour by hour (observed minutes below 60 are the partial first and last hours and the fragment). "Area radio receipts" in both tables counts relayed radio receipts at MSA gateways, those carrying `relay_node`; they sum to 5 760.

| hour UTC | local CDT | segment | observed min | records | radio deliveries | internet copies | area radio receipts | contract 500 m | gateway-only | positions ignored |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10-22T16Z | Thu 11 | main | 48 | 1180706 | 55764 | 1110178 | 56 | 0 | 4 | 6 |
| 10-22T17Z | Thu 12 | main | 60 | 1486967 | 77403 | 1390762 | 65 | 0 | 5 | 5 |
| 10-22T18Z | Thu 13 | main | 60 | 1758414 | 82812 | 1656612 | 90 | 0 | 5 | 8 |
| 10-22T19Z | Thu 14 | main | 60 | 1426480 | 63239 | 1345271 | 68 | 0 | 3 | 5 |
| 10-22T20Z | Thu 15 | main | 60 | 1436432 | 63978 | 1354187 | 92 | 1 | 10 | 13 |
| 10-22T21Z | Thu 16 | main | 60 | 1409034 | 66451 | 1324423 | 61 | 1 | 4 | 6 |
| 10-22T22Z | Thu 17 | main | 60 | 1417259 | 63946 | 1334761 | 81 | 2 | 6 | 6 |
| 10-22T23Z | Thu 18 | main | 60 | 1493481 | 64443 | 1410926 | 108 | 2 | 7 | 8 |
| 10-23T00Z | Thu 19 | main | 60 | 1553585 | 73641 | 1461330 | 96 | 0 | 5 | 8 |
| 10-23T01Z | Thu 20 | main | 60 | 1332285 | 63638 | 1250327 | 84 | 1 | 4 | 5 |
| 10-23T02Z | Thu 21 | main | 60 | 1362707 | 65907 | 1278510 | 78 | 0 | 5 | 6 |
| 10-23T03Z | Thu 22 | main | 60 | 1327167 | 64433 | 1244141 | 94 | 0 | 2 | 4 |
| 10-23T04Z | Thu 23 | main | 60 | 1421847 | 66975 | 1336404 | 97 | 0 | 5 | 5 |
| 10-23T05Z | Fri 00 | main | 60 | 1460783 | 66079 | 1375998 | 107 | 1 | 3 | 4 |
| 10-23T06Z | Fri 01 | main | 60 | 1335086 | 59612 | 1258244 | 79 | 0 | 5 | 7 |
| 10-23T07Z | Fri 02 | main | 60 | 1508397 | 68935 | 1420736 | 96 | 0 | 5 | 9 |
| 10-23T08Z | Fri 03 | main | 60 | 1381735 | 66079 | 1297171 | 139 | 1 | 6 | 6 |
| 10-23T09Z | Fri 04 | main | 60 | 1477479 | 68069 | 1390522 | 104 | 1 | 5 | 7 |
| 10-23T10Z | Fri 05 | main | 60 | 1474865 | 67905 | 1387619 | 98 | 0 | 2 | 4 |
| 10-23T11Z | Fri 06 | main | 60 | 1674165 | 69255 | 1585929 | 120 | 1 | 5 | 7 |
| 10-23T12Z | Fri 07 | main | 60 | 1290213 | 66013 | 1204936 | 131 | 0 | 2 | 7 |
| 10-23T13Z | Fri 08 | main | 60 | 1299021 | 64851 | 1215051 | 121 | 0 | 6 | 10 |
| 10-23T14Z | Fri 09 | main | 60 | 1286672 | 64691 | 1202792 | 68 | 0 | 4 | 7 |
| 10-23T15Z | Fri 10 | main | 60 | 1296139 | 65540 | 1211865 | 105 | 2 | 10 | 14 |
| 10-23T16Z | Fri 11 | main | 60 | 1135843 | 63123 | 1053916 | 73 | 0 | 3 | 6 |
| 10-23T17Z | Fri 12 | main | 60 | 1209760 | 64614 | 1125703 | 83 | 0 | 4 | 10 |
| 10-23T18Z | Fri 13 | main | 60 | 1219574 | 70949 | 1128600 | 72 | 0 | 7 | 8 |
| 10-23T19Z | Fri 14 | main | 60 | 1139370 | 65141 | 1055060 | 84 | 1 | 5 | 7 |
| 10-23T20Z | Fri 15 | main | 60 | 1156336 | 68625 | 1068027 | 72 | 0 | 5 | 6 |
| 10-23T21Z | Fri 16 | main | 60 | 1386744 | 67097 | 1224100 | 82 | 1 | 4 | 6 |
| 10-23T22Z | Fri 17 | main | 60 | 1216048 | 64888 | 1040179 | 74 | 1 | 7 | 7 |
| 10-23T23Z | Fri 18 | main | 60 | 1140618 | 65560 | 960668 | 113 | 2 | 4 | 5 |
| 10-24T00Z | Fri 19 | main | 60 | 1116842 | 69400 | 924662 | 92 | 2 | 6 | 9 |
| 10-24T01Z | Fri 20 | main | 60 | 1227098 | 64792 | 1112365 | 135 | 1 | 6 | 8 |
| 10-24T02Z | Fri 21 | main | 60 | 1423116 | 65700 | 1279921 | 135 | 1 | 8 | 9 |
| 10-24T03Z | Fri 22 | main | 60 | 1141563 | 66155 | 968112 | 97 | 1 | 6 | 9 |
| 10-24T04Z | Fri 23 | main | 60 | 1120625 | 66582 | 940129 | 111 | 1 | 7 | 11 |
| 10-24T05Z | Sat 00 | main | 60 | 1092940 | 67283 | 912502 | 94 | 1 | 4 | 8 |
| 10-24T06Z | Sat 01 | main | 60 | 1142658 | 66248 | 963615 | 99 | 1 | 7 | 9 |
| 10-24T07Z | Sat 02 | main | 60 | 1170379 | 67291 | 989053 | 71 | 1 | 3 | 4 |
| 10-24T08Z | Sat 03 | main | 60 | 1198185 | 79691 | 1002692 | 85 | 1 | 4 | 5 |
| 10-24T09Z | Sat 04 | main | 60 | 1043111 | 63995 | 952291 | 78 | 1 | 3 | 7 |
| 10-24T10Z | Sat 05 | main | 60 | 1095167 | 68735 | 1006621 | 81 | 2 | 5 | 5 |
| 10-24T11Z | Sat 06 | main | 60 | 1070639 | 64292 | 986817 | 78 | 0 | 5 | 6 |
| 10-24T12Z | Sat 07 | main | 60 | 1114275 | 64857 | 1029514 | 115 | 0 | 6 | 7 |
| 10-24T13Z | Sat 08 | main | 60 | 1230323 | 65504 | 1145565 | 83 | 0 | 7 | 9 |
| 10-24T14Z | Sat 09 | main | 60 | 1158033 | 66770 | 1071729 | 108 | 2 | 5 | 9 |
| 10-24T15Z | Sat 10 | main | 60 | 1161357 | 74078 | 1066941 | 101 | 1 | 5 | 8 |
| 10-24T16Z | Sat 11 | main | 60 | 1418700 | 70330 | 1328813 | 77 | 1 | 7 | 7 |
| 10-24T17Z | Sat 12 | main | 60 | 1080587 | 67121 | 993549 | 67 | 0 | 4 | 6 |
| 10-24T18Z | Sat 13 | main | 60 | 1052580 | 54907 | 981990 | 66 | 0 | 4 | 5 |
| 10-24T19Z | Sat 14 | main | 60 | 1159476 | 68657 | 1065484 | 77 | 0 | 4 | 7 |
| 10-24T20Z | Sat 15 | main | 60 | 1210026 | 68050 | 1032015 | 101 | 1 | 5 | 7 |
| 10-24T21Z | Sat 16 | main | 60 | 1235317 | 67567 | 1055148 | 100 | 1 | 7 | 10 |
| 10-24T22Z | Sat 17 | main | 60 | 1169796 | 66693 | 997025 | 70 | 1 | 4 | 7 |
| 10-24T23Z | Sat 18 | main | 60 | 1145678 | 69626 | 964076 | 80 | 1 | 6 | 7 |
| 10-25T00Z | Sat 19 | main | 60 | 1499689 | 66430 | 1317756 | 77 | 0 | 5 | 8 |
| 10-25T01Z | Sat 20 | main | 60 | 1114753 | 65475 | 988074 | 77 | 1 | 4 | 4 |
| 10-25T02Z | Sat 21 | main | 60 | 1062451 | 67580 | 975000 | 93 | 0 | 4 | 5 |
| 10-25T03Z | Sat 22 | main | 60 | 1353778 | 68975 | 1265215 | 85 | 0 | 8 | 8 |
| 10-25T04Z | Sat 23 | main | 60 | 1165788 | 65846 | 1080366 | 91 | 1 | 3 | 3 |
| 10-25T05Z | Sun 00 | main | 60 | 1227274 | 68442 | 1139003 | 66 | 0 | 1 | 3 |
| 10-25T06Z | Sun 01 | main | 60 | 1170480 | 63764 | 1087171 | 92 | 1 | 6 | 9 |
| 10-25T07Z | Sun 02 | main | 43 | 983513 | 47092 | 922441 | 82 | 0 | 4 | 6 |
| 10-28T20Z | Wed 15 | fragment 1 | 4 | 87195 | 4563 | 63918 | 5 | 0 | 0 | 0 |

## Subscription scope: metro radio receipts by topic root

This table decides ADR 0005 item 7. Full public capture, distinct radio receipts at gateways positioned in the MSA, by the topic root (the segment after `msh/US`) they arrived under:

| topic root | radio receipts | share | with relay_node | share |
| --- | --- | --- | --- | --- |
| msh/US/MO | 296 | 4.4% | 130 | 2.3% |
| msh/US/IL | 0 | 0.0% | 0 | 0.0% |
| msh/US/MeshSTL | 6 234 | 93.3% | 5 479 | 95.1% |
| other msh/US | 151 | 2.3% | 151 | 2.6% |

All 151 "other" receipts arrived under `msh/US/2`, the default root with no region segment. In the table below, MO, IL and MeshSTL together keep all 320 gateway-only and all 447 positions-ignored link-hours, so receipts under `msh/US/2` add none in either row. Link-hours kept when only receipts under the given roots are counted (positions and relayer candidates still from the whole capture), per week weighted:

| receipts counted from | gateway-only | share | per week | positions ignored | share | per week |
| --- | --- | --- | --- | --- | --- | --- |
| msh/US/# (everything) | 320 | 100.0% | 852 | 447 | 100.0% | 1 194 |
| MO, IL and MeshSTL | 320 | 100.0% | 852 | 447 | 100.0% | 1 194 |
| MO only | 4 | 1.2% | 13 | 4 | 0.9% | 13 |
| IL only | 0 | 0.0% | 0 | 0 | 0.0% | 0 |
| MeshSTL only | 316 | 98.8% | 839 | 443 | 99.1% | 1 181 |

A simulated subscription (`--roots MO,IL,MeshSTL`, so positions and relayer candidates also come only from those roots), MSA, per week weighted against the full subscription: keep 30 minutes 104 against 110 at 500 m and 101 against 107 at 3 km; stationarity 185 against 168 and 178 against 162; gateway-only 852 and positions ignored 1 194, both unchanged. Relayer resolution barely moves (4 604 resolved receipts become 4 601), so the changes come from positions: with fewer positions, usable MSA nodes fall from 58 to 55 and stationary ones from 35 to 34, a different set of nodes counts as stationary, and relayers usable but outside the 30-minute window rise from 344 to 358. The 30-minute rule loses 2 of 42 link-hours; the stationarity rule gains. The raw volume falls from 5.568 GB a day to 0.017 GB a day. Dropping IL as well (`--roots MO,MeshSTL`) gives the same MSA link-hours in every row (two funnel rows differ by one receipt), so IL's positions and relayer candidates feed no metro link, and the volume falls to 116 260 records, 34.7 MB, 0.013 GB a day.

## Volume

Public broker, full capture, uncompressed JSON lines as the spike stores them (base64 payload), over 63.594 observed hours:

| copy class | records | GB | share of bytes | GB per day |
| --- | --- | --- | --- | --- |
| e, internet copy (via_mqtt, header-only except 1 204) | 74 314 521 | 13.080 | 88.6% | 4.936 |
| e, radio | 4 262 147 | 0.996 | 6.7% | 0.376 |
| e, unparsed or no sender | 2 012 250 | 0.390 | 2.6% | 0.147 |
| json | 448 218 | 0.214 | 1.4% | 0.081 |
| map | 240 823 | 0.066 | 0.4% | 0.025 |
| other and stat | 60 645 | 0.010 | 0.1% | 0.004 |
| all | 81 338 604 | 14.755 | 100.0% | 5.568 |

By topic root, the three regional roots together carry 158 099 records and 45.3 MB, 0.017 GB a day; everything else is under other `msh/US` roots. Only 9 internet copies arrive under the regional roots in 63.594 observed hours, all under `msh/US/IL`; none arrive under MO or MeshSTL. Of all 74 314 521 internet copies, 1 204 keep a payload (116 default key, 1 077 plaintext, 11 PKI). Internet copies kept as counts per (hour, topic, gateway) instead: 358 871 rows over 65 clock hours, median 3 978 rows an hour, about 135 000 rows a day. The MeshSTL broker, its 24 hours: 23 946 records, 6.5 MB, 0.006 GB a day.

Compression, the 64 gzipped public hours (2026-10-22T16 to 2026-10-25T07, the main segment of 63.527 observed hours; the 2026-10-28T20 fragment stays plain JSON lines and is not converted). Each hour was converted on its own to Parquet with zstd (pyarrow 21.0.0, columns `received_at` as a UTC timestamp, `topic`, `payload` as decoded bytes), read back, and compared with its `.gz` on row count and on a sha256 over every (received_at, topic, payload) in file order before the next hour started. All 64 hours matched. The `.gz` files are kept.

| form | GB | ratio to JSON lines | GB per day |
| --- | --- | --- | --- |
| JSON lines, uncompressed | 14.728 | 1.00 | 5.564 |
| gzip, as archived | 2.740 | 5.37 | 1.035 |
| Parquet, zstd | 0.583 | 25.27 | 0.220 |

Rows: 81 251 409, from 81 251 411 lines; the 2 others are truncated JSON lines in 2026-10-25T07, written at 07:42:45Z and 07:43:14Z as Docker Desktop stopped, and `analyze.py` counts the same 2 as unreadable. With the fragment's 87 195 lines that is the 81 338 604 records in the table above. Parquet is 4.70 times smaller than gzip; per hour it runs from 7.0 to 13.9 MB, and the larger hours are not the busier ones, since payload bytes compress less than the repeated topics and header-only copies. The per-hour table is in the PR #9 description.

## Public broker details, full capture

(a) Envelopes by via_mqtt:

| measure | radio deliveries | gateway's own excluded | via_mqtt true |
| --- | --- | --- | --- |
| envelopes | 4 262 147 | 2 381 368 | 74 314 521 |
| header-only (status stripped) | 25.8% | 39.7% | 100.0% |
| `relay_node` set | 77.5% | 87.1% | 0.01% |
| `relay_node` set, among header-only | 84.1% | 97.9% | 0.01% |
| own low byte, among relayed | 58.0% | 33.1% | 0.2% |
| hop_limit zero | 87.9% | 91.7% | 100.0% |

Decode status, all envelopes: stripped 93.3%, ok 3.3%, malformed 2.5%, decrypt_failed 0.9%. On radio deliveries: ok 57.2% (default key 47.3%, plaintext 9.9%), stripped 25.8%, wrong key 10.2%, PKI 6.9%. Distinct radio receipts, gateway's own excluded: 546 360, of which 461 099 carry `relay_node`; those resolve to one sender heard by the same gateway in 63.6%, several in 19.8%, none in 16.6%. The OK to MQTT bit is set on 74.8% of packets that keep a payload.

Decoded portnums: Position 38.9%, NodeInfo 20.2%, Telemetry 14.7%, MapReport 8.6%, Routing 8.1%, Text 5.3% (142 003 TEXT_MESSAGE_APP packets), Traceroute 4.1%. Seven more appear once each: three negative numbers, which are wrong-key noise that happened to parse (`analyze.py` rejects portnum 0 but not a negative one; the Phase 1 decoder rejects portnum 0 or below), SERIAL_APP, NODE_STATUS_APP and the unassigned 92 and 102. NeighborInfo 0. Position packets at 20 bits or more: 142, from 20 nodes nationally.

precision_bits on Position packets: national 15 or 16 bits 35.0%, 13 bits 40.7%; inside the MSA 15 or 16 bits 35.7%, 13 bits 39.1%, nothing above 16.

Regional segments on the public broker, radio deliveries on `e` topics (parsed, with a sender, via_mqtt false):

| topic root | packets | per hour | gateways | senders | relayed | with payload | positions |
| --- | --- | --- | --- | --- | --- | --- | --- |
| msh/US/MeshSTL | 18 378 | 289 | 68 | 282 | 38.9% | 67.3% | 4 311 |
| msh/US/MO | 20 590 | 324 | 20 | 511 | 30.5% | 81.1% | 1 955 |
| msh/US/IL | 30 828 | 485 | 101 | 715 | 54.5% | 95.4% | 15 157 |

Another 28 342 records on `msh/US/MeshSTL` `e` topics do not parse as a `ServiceEnvelope` or have no sender: some MeshSTL gateways publish other payloads there. They are in the volume table as "e, unparsed or no sender" and in no link figure.

Funnel, distinct radio receipts, combined rule, keep 30 minutes:

| step | MSA | MO plus IL |
| --- | --- | --- |
| radio receipts with `relay_node` at area gateways | 5 760 | 20 749 |
| `lnk_06` fail: signal missing or out of range | 17 | 533 |
| `lnk_01` fail: relayer not resolved | 1 139 | 6 828 |
| `lnk_03` fail: relayer is the gateway | 0 | 14 |
| `lnk_01` and `lnk_03` pass | 4 604 | 13 374 |
| `lnk_02` fail: relayer, no decodable position in the capture | 2 328 | 3 998 |
| `lnk_02` fail: relayer, below 15 bits | 943 | 5 297 |
| `lnk_02` fail: relayer, position only outside the area | 16 | 824 |
| `lnk_02` fail: relayer, usable but outside the window | 344 | 1 802 |
| `lnk_02` fail: gateway, below 15 bits | 215 | 512 |
| `lnk_02` fail: gateway, usable but outside the window | 86 | 133 |
| `lnk_02` pass | 672 | 808 |
| `lnk_05` fail: boxes overlap, distance 0 m to the box diagonal | 355 | 442 |
| `lnk_05` fail: over 60 km | 0 | 24 |
| `lnk_05` pass | 317 | 342 |
| link-hours with 3 or more receipts, 500 m | 42 | 45 |

Relayers that failed `lnk_02` with no decodable position over the whole capture: 33 in the MSA (31 sent other decoded packets), 94 in MO plus IL (87 did). Overlap diagonals: MSA median 1 852 m, max 1 852 m. Stationary usable nodes: 35 of 58 in the MSA, 92 of 168 in MO plus IL.

(2a) Time to first usable position, full capture:

| measure | MSA relayers | all radio senders |
| --- | --- | --- |
| nodes | 76 | 16 073 |
| no decodable position in the capture | 33 (43.4%) | 9 515 (59.2%) |
| positions only below 15 bits | 28 (36.8%) | 4 808 (29.9%) |
| reached a usable position | 15 (19.7%) | 1 750 (10.9%) |
| hours to first usable: p50, p75, p90, max | 0.5, 4.5, 12.6, 27.0 | 0.0, 4.0, 20.3, 148.5 |
| histogram 0 to 1, 1 to 2, 2 to 4, 4 to 8, 8 to 12, 12 to 24, 24 to 48, 48 to 72, 72 to 168 h | 9, 0, 2, 2, 0, 1, 1, 0, 0 | 1 117, 88, 108, 87, 70, 134, 107, 38, 1 |

The waits are wall clock from first radio receipt to first usable position, so the one sender at 148.5 hours was first placed in the fragment; nothing else crosses the gap. "Not yet" and "not at all" are now told apart: none of the 33 MSA relayers with no position sent one in 63.59 hours, while every MSA relayer that did was placed within 27 hours of its first receipt.

Sensitivity, combined rule, MSA, per week weighted at 500 m (not the contract rules): 30 minutes with 3 packets 110; with 1 packet 272 (6 pairs); 24 hours with 3 packets 193; with 1 packet 494 (8 pairs).

## MeshSTL broker details, its 24 hours

No envelope arrives header-only and none is a via_mqtt copy: the broker forwards what its gateways upload. `relay_node` is set on 83.5% of radio deliveries (gateway's own excluded) and resolves to one sender heard by the same gateway for 77.6% of the 4 630 relayed receipts, several for 12.5%, none for 9.9%. Decode status on radio deliveries: ok 76.3% (default key 73.5%, plaintext 2.8%), wrong key 22.5% (MeshSTL's own channel, whose key is not used), PKI 0.9%.

Decoded portnums: Position 27.5%, Routing 19.0%, NodeInfo 17.6%, Traceroute 13.6%, Telemetry 8.1%, Store and Forward 6.9%, MapReport 6.2%, NeighborInfo 0.6%, Text 0.2% (38 packets), ATAK plugin 0.2%, Node status 0.2%; 11 in all, 54 packets unknown_port.

NeighborInfo arrives: 104 packets (4.3 an hour), 11 reporters, 28 distinct (neighbour, reporter) pairs, median SNR 6.0 dB; 6 reporters are positioned in the MSA. As a link source on its own it gives 24 link-hours at 500 m (1 pair) and 0 at 3 km; in the combined rule it adds nothing over `relay_node`.

precision_bits on Position packets: 15 bits or more on 40.8% nationally and 39.4% inside the MSA, against 35.7% in the MSA on the public broker; 17 to 19 bits on 4.5%; 32 bits on 4.2%. Position packets at 20 bits or more: 200, from 8 nodes.

Funnel, MSA, combined rule, keep 30 minutes: 3 863 receipts with `relay_node`; 3 `lnk_06` failures; 676 unresolved; 3 184 resolved; relayer with no position 693, below 15 bits 993, outside the window 182; gateway below 15 bits 294, outside the window 103; 919 pass `lnk_02`; 200 overlapping boxes; 719 at 500 m to 60 km; 74 link-hours. Relayers resolved in the MSA: 58, of which 22 (37.9%) sent no decodable position (the funnel's relayer count is 23 because it uses the combined rule, and one relayer is resolved only through NeighborInfo), 18 only below 15 bits and 18 a usable one, p90 0.9 hours after their first receipt. Stationary usable nodes: 29 of 48.

## Positions across brokers, shared 24 hours

- Public receipts with MeshSTL positions added: 121 nodes are positioned on MeshSTL, 8 of them not positioned on the public broker in the shared 24 hours, all 8 usable. MSA relayers failing `lnk_02` with no position fall from 26 to 22, and keep 30 minutes at 500 m rises from 21 to 44 link-hours (177 to 344 a week). The 3 km figures do not move.
- MeshSTL receipts with public positions added: 5 601 nodes are positioned on the public broker, 154 of them usable inside the analysis's keep box (Missouri plus Illinois with a margin, longitude -96.0 to -86.9, latitude 35.8 to 42.7) and absent from MeshSTL, but none is a metro relayer: the 23 MSA relayers with no position stay 23, and only gateway-only moves (200 to 201).
- So the MeshSTL broker places relayers the public broker cannot, and the public broker adds nothing the MeshSTL broker lacks in the metro.
