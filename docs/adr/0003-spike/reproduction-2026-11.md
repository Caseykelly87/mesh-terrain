# Reproduction of the ADR 0003 figures from the pseudonymised snapshot, November 2026

The reproduction gate passed on 2026-11-14 (04:10 to 04:12 UTC): every ADR 0003 link figure, recomputed from snapshot `meshtastic-capture-2026-10-22` alone, equals the same figure computed from the real-id archive under the snapshot's design rules. This file records what was compared and what it showed. The full output is `C:/Users/Casey/mesh-terrain-data/gate-reference/gate-2026-11-14/gate.md` (on the deletion list); the aggregates behind each column are under `reference/` beside this file.

## What the gate compares

Casey's rule of 2026-11-13: four figures for every run and figure.

- (a) `results.md`, the spike's published figures. They include the 4-minute public fragment of 2026-10-28T20.
- (b) `analyze.py`, unchanged, on the archive without that fragment (`reference/reference-without-fragment.json`). Run first with the fragment, it reproduced every (a) figure exactly, which proves the reference code is the spike's analysis.
- (c) the same with the snapshot's four design rules (`reference/reference-design-rules-copies-repeats-shared-unparsed.json`, read R10):
  - copies: an internet copy (a delivery the gateway received over MQTT, `via_mqtt`) that kept its payload is counted, not used;
  - repeats: identical deliveries from one broker, the same topic and payload bytes, are merged into the first sighting;
  - shared: a delivery both brokers carried is timed at its first sighting on either broker;
  - unparsed: a delivery on an `e` or `map` topic whose envelope does not parse, or whose sender is 0, is counted as `unparsed` and not used.
- (d) the snapshot: pseudonymised Parquet, read after its sha256 manifest check. Each pseudonym becomes a stand-in number that keeps the node's low byte, so `analyze.py` resolves relayers exactly as on real ids.

The gate passes only when (c) equals (d) in every figure. It does: 56 link figures over seven runs, and 7 counts.

## Headline figures, MSA, link-hours a week

Weekday-weighted, with observed link-hours and distinct (relayers, gateways, pairs). (c) and (d) are identical, so they share a column.

| run, figure | (a) results.md | (b) raw | (c) = (d) |
| --- | --- | --- | --- |
| public, full capture, keep 30 minutes, 500 m | 110 (42; 2, 4, 4) | 111 (42; 2, 4, 4) | 100 (38; 1, 3, 3) |
| public, full capture, keep 30 minutes, 3 km | 107 (41; 1, 3, 3) | 107 (41; 1, 3, 3) | 100 (38; 1, 3, 3) |
| public, full capture, stationarity, 500 m | 168 (62; 4, 6, 6) | 168 (62; 4, 6, 6) | 159 (59; 3, 5, 5) |
| public, full capture, stationarity, 3 km | 162 (60; 3, 5, 5) | 162 (60; 3, 5, 5) | 159 (59; 3, 5, 5) |
| public, full capture, gateway-only | 852 (320; 23, 16, 28) | 853 (320; 23, 16, 28) | 744 (275; 22, 16, 27) |
| public, observed hours | 63.594 | 63.527 | 63.527 |
| public, shared 24 h, keep 30 minutes, 500 m and 3 km | 177 (21; 1, 2, 2) | 177 | 177 |
| public, shared 24 h, stationarity, 500 m and 3 km | 228 (29; 2, 2, 3) | 228 | 228 |
| MeshSTL, 24 h, keep 30 minutes, 500 m | 560 (74; 6, 6, 7) | 560 | 469 (57; 6, 6, 7) |
| MeshSTL, 24 h, keep 30 minutes, 3 km | 281 (32; 4, 4, 5) | 281 | 281 (32; 4, 4, 5) |
| MeshSTL, 24 h, stationarity, 500 m | 617 (89; 6, 6, 7) | 617 | 499 (68; 6, 6, 7) |
| MeshSTL, 24 h, stationarity, 3 km | 309 (42; 4, 4, 5) | 309 | 309 (42; 4, 4, 5) |
| MeshSTL, 24 h, gateway-only | 1 502 (200; 26, 14, 28) | 1 502 | 1 465 (192; 24, 13, 26) |
| public with MeshSTL positions, keep 30 minutes, 500 m | 344 | 344 | 338 |
| roots MO, IL and MeshSTL, keep 30 minutes, 500 m | 104 | 104 | 94 |

Counts per broker, (c) = (d): public TEXT_MESSAGE_APP rows 7 923, internet copies counted 74 250 603, stored deliveries 4 486 141, repeats merged 3 595 118; MeshSTL text rows 19, stored deliveries 22 256, repeats merged 11 409.

## Why (b) and (c) differ

- The fragment, (a) to (b): 0.067 observed hours fewer, which lifts four weekly rates by one. No observed count changes.
- Repeats, nearly all of (b) to (c). 3 595 118 of the public broker's 4 486 141 stored deliveries, and 11 409 of MeshSTL's 22 256, are byte-identical repeats. Every merged group was checked byte for byte, and none differ (read R4). Later repeats of Position and MapReport packets gave `analyze.py` fresh position samples, so fewer receipts pass `lnk_02` (both ends usable within 30 minutes). Relayer resolution does not change.
- Copies: moves no figure (1 192 public copies kept a payload).
- Shared: 78 MeshSTL deliveries, 77 of them MapReports, were sighted on the public broker a median 28 hours earlier. Timed there, two weekday MeshSTL link-hours (2026-10-23T22 and T23) lose the relayer's position, 504 to 469 a week, all of the difference (read R4).
- Unparsed: moves only the public text count, 7 986 to 7 923. The 63 are public plaintext text packets with sender 0, which `analyze.py` counted and the decoder does not store (read R9).

## Also measured from the snapshot

- SNR: rx_snr 0.0 with RSSI set, public 2 281 and MeshSTL 30. Both null, public 356 470 and MeshSTL 5 383. The same counts came from the archive in R4.
- Merged counts: `merged_cross_broker` public 3 001, MeshSTL 78; `merged_repeat` public 3 595 118, MeshSTL 11 409.
- The portnum rule: deliveries made `decrypt_failed` by a decrypted portnum of 0 or above the enum's maximum, public 921 (811 rows), MeshSTL 12 (6 rows). The spike's wrong-key count, which also includes payloads that fail to parse, was 432 589 and 4 768.
- Both-ends pairs under the stationarity rule at 500 m: public 5, MeshSTL 7, 4 shared, union 8.

## Reads of the real-id archive

- R1: the spike analysis, with and without the fragment.
- R2: the reduction.
- R3: sha256 of the deletion list.
- R4: the repeat and shared-rule diagnostic.
- R5: one hour, for the memory measurement.
- R6: the reference outputs, by pattern counts.
- R7: the key search.
- R8: the three-rule (c), regenerated to check the refactor.
- R9 and R9b: sender-0 packets and the retain flag.
- R10: the four-rule (c).

Every output is aggregates only. Each read was stated in a brief before it ran.
