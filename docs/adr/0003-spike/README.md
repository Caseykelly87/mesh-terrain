# Phase 1 spike for ADR 0003

Evidence for `docs/adr/0003-direct-link-and-position-source.md`: a 48-hour capture of the public Meshtastic broker, a decoder, and the measurements the plan's Phase 1 spike asks for. Nothing here is production code; the ingester is built separately in Phase 1.

## Broker and topics

From the Meshtastic docs (`https://meshtastic.org/docs/software/integrations/mqtt/` and `https://meshtastic.org/docs/configuration/module/mqtt/`): host `mqtt.meshtastic.org`, port 1883, username `meshdev`, password `large4cats`. Gateways publish protobuf `ServiceEnvelope` messages to `msh/REGION/2/e/CHANNELNAME/!gatewayid`. The default root for the United States is `msh/US`, and many communities insert a state or group segment (`msh/US/MO/2/e/...`, `msh/US/MeshSTL/2/e/...`). JSON copies go to `.../2/json/...`, map reports to `.../2/map/`, and status strings to `.../2/stat/`.

The capture subscribes to `msh/US/#`. Measured over the first 4 hours: about 1.5 million messages and 270 MB of JSON lines an hour, about 13 GB for 48 hours. That is under the 20 GB line in the brief, so nothing was narrowed. Free space on C: was 34 GB on 2026-10-22.

## Files

- `capture.py`: subscribes, buffers, and appends one JSON line per delivery (`received_at` in UTC from our clock, `topic`, `payload_b64`) to `capture-YYYY-MM-DDTHH.jsonl`. It flushes every 60 seconds or 20 000 records and reconnects with backoff from 1 to 120 seconds. JSON lines rather than Parquet, for three reasons: an append-only text file survives a crash mid-hour with every flushed record readable, it needs no writer state, and the landing format belongs to the ingester, not the spike. It decodes nothing and logs no payload or topic.
- `analyze.py`: tallies each hourly file in its own worker process and decrypts default-key packets. The cipher is AES-128-CTR with key `1PG7OiApB1nwvP+rz05pAQ==`; the nonce is the packet id as 8 bytes little-endian, then the sender id as 4 bytes little-endian, then 4 zero bytes. It decodes Position, NodeInfo, Telemetry, NeighborInfo and MapReport, then prints for each area: (a) split by via_mqtt, (b), (c), regional topics, counts, the (e) yield with a funnel that names each loss, a position-free diagnostic, a sensitivity table and the (f) check. Output is aggregates only; no node id, name or coordinate is printed.
- `Dockerfile`: one image for both scripts, with spike-only pins.

## Commands

Build the image and start the capture (from the repository root, Git Bash):

```
docker build -t mesh-spike docs/adr/0003-spike
mkdir -p C:/Users/Casey/mesh-terrain-data/spike-capture
docker run -d --name mesh-spike-capture --restart unless-stopped \
  -v C:/Users/Casey/mesh-terrain-data/spike-capture:/data \
  -e MQTT_HOST=mqtt.meshtastic.org -e MQTT_PORT=1883 \
  -e MQTT_USER=meshdev -e MQTT_PASSWORD=large4cats \
  -e "MQTT_TOPICS=msh/US/#" mesh-spike
```

Check that it is alive: one `flush` line a minute with `count`, `bytes`, `path`, `duration_s` and `last_message_age_s`, and one new file an hour.

```
docker ps --filter name=mesh-spike-capture
docker logs --since 5m mesh-spike-capture
ls -la C:/Users/Casey/mesh-terrain-data/spike-capture
```

Stop it. SIGTERM flushes the buffer first, and the data stays on disk.

```
docker stop mesh-spike-capture
```

Run the analysis. The committed scripts are mounted over the image's copies, so the image the capture runs from never needs a rebuild. `MSYS_NO_PATHCONV=1` stops Git Bash from rewriting `/data` into a Windows path. It takes about 70 seconds for 4 hours of data on 12 cores.

```
MSYS_NO_PATHCONV=1 docker run --rm \
  -v C:/Users/Casey/mesh-terrain-data/spike-capture:/data:ro \
  -v C:/Users/Casey/mesh-terrain-data:/bounds:ro \
  -v "$(pwd -W)/dbt/seeds:/seeds:ro" \
  -v "$(pwd -W)/docs/adr/0003-spike:/spike:ro" \
  mesh-spike python /spike/analyze.py /data /seeds/msa_boundary.csv /bounds/states_mo_il.csv
```

## Boundaries

- MSA: the merged seed `dbt/seeds/msa_boundary.csv`, sha256 `711054e1cfc2962a26db8e6c4b385468d759ff81172fd905f57c60e916925aea`, the value warehouse-eng recorded in PR #7. The seed splits the WKT over two rows by `part`, and `analyze.py` joins the chunks in `part` order. The view `staging.stg_msa_boundary` gives the same polygon, but the spike container does not connect to the warehouse.
- Missouri (GEOID 29) and Illinois (GEOID 17) come from `https://www2.census.gov/geo/tiger/TIGER2025/STATE/tl_2025_us_state.zip`, sha256 `59a220888a8d9be8117c4fcd38f542bd02d81abf0d198c78113595ad540dd957`. They were converted with pyshp 2.3.1 and shapely 2.2.0 in a throwaway `python:3.12-slim-bookworm` container, the same way the CBSA seed was made. The output is `C:/Users/Casey/mesh-terrain-data/states_mo_il.csv` (columns `geoid`, `name`, `source`, `geometry_wkt`), sha256 `8c0749d467869d9a5a4b71898958b006edc78bf15b6984b1b0e997bfc43da45b`. Both MultiPolygons are valid. The file stays outside the repo.

## Rule definitions used for (e)

- Receipts are radio envelopes only (via_mqtt false), the gateway's own packets excluded, with `relay_node` set. Each distinct (gateway, sender, packet id) counts once. Candidates for the relayer are the senders the receiving gateway heard by radio anywhere in the capture; the ADR proposes the trailing 24 hours for production.
- `lnk_06`: `rx_rssi` set and in [-140, 0] dBm, `rx_snr` in [-25, 15] dB.
- (i) `relay_node`: if the byte equals the sender's own low byte, the relayer is the sender. Otherwise it is the one candidate with that low byte, or unresolved when there are none or several. `relay_node_strict` drops the own-byte shortcut.
- (ii) NeighborInfo: the candidates are the neighbours listed in the gateway's own NeighborInfo reports, matched the same way.
- (iii) combined: (i) or (ii), dropped when both resolve and disagree.
- `lnk_02`: the usable position closest in time for each end, within 30 minutes. A failure is attributed to the relayer first, then the gateway, under one of four causes: no position in the capture, a position only outside the area, below 15 bits, or usable but outside the window.
- `lnk_05`: the geodesic distance on WGS84 between the two box centres, in degrees as sent. 0 m means both ends are in the same quantised box.
- `lnk_04` and the 3 km test: at least 3 receipts in the (relayer, gateway, clock hour) group, tested on the median distance of the group.

## Interim results (4.05 hours, weekly figures extrapolated x41.47)

(a) Envelopes by via_mqtt:

| measure | radio | gateway's own excluded | via_mqtt true |
| --- | --- | --- | --- |
| envelopes | 294 581 | 176 841 | 5 838 465 |
| header-only | 33.2% | 49.5% | 100.0% |
| `relay_node` set | 77.7% | 85.5% | 0.02% |
| `relay_node` set, among header-only | 84.0% | 93.8% | 0.02% |
| own low byte, among relayed | 52.3% | 27.9% | 0.4% |
| `hop_limit` zero | 88.5% | 92.7% | 99.9% |

Decode status on radio envelopes: ok 51.3 percent (default key 42.7, plaintext 8.6), header-only 33.2, wrong key 8.9, PKI 6.6. Every via_mqtt copy is header-only.

Funnel, distinct radio receipts, combined rule:

| step | MSA | MO plus IL |
| --- | --- | --- |
| radio receipts with `relay_node` at area gateways | 278 | 755 |
| `lnk_06` fail: signal missing or out of range | 1 | 5 |
| `lnk_01` fail: relayer not resolved | 70 | 254 |
| `lnk_01` and `lnk_03` pass | 207 | 496 |
| `lnk_02` fail: relayer, no position in capture | 140 | 305 |
| `lnk_02` fail: relayer, below 15 bits | 33 | 139 |
| `lnk_02` fail: relayer, usable but outside the window | 5 | 5 |
| `lnk_02` fail: gateway, below 15 bits | 11 | 24 |
| `lnk_02` fail: gateway, usable but outside the window | 5 | 5 |
| `lnk_02` pass | 13 | 18 |
| `lnk_05` fail: 0 m, same box | 12 | 12 |
| `lnk_05` fail: under 500 m | 0 | 5 |
| `lnk_05` pass | 1 | 1 |
| link-hours with 3 or more receipts | 0 | 0 |

Relayers that failed `lnk_02` because they never sent a position: 21 in the MSA, 19 of them sent other decoded packets. In Missouri plus Illinois: 45, of which 43.

Position-free diagnostic (`lnk_02` and `lnk_05` ignored, combined rule):

| measure | MSA | MO plus IL |
| --- | --- | --- |
| (relayer, gateway, hour) groups with 3 or more receipts | 20 | 47 |
| per week | 829 | 1 949 |
| distinct pairs, relayers, gateways | 9, 8, 7 | 22, 20, 19 |
| pairs whose relayer never sent a position | 6 | 15 |
| pairs with both ends ever usable | 1 | 1 |

Sensitivity, combined rule, MSA (not the contract rules), per week at 500 m:

| position window | min packets 3 | min packets 1 |
| --- | --- | --- |
| 30 min (contract) | 0 | 41 (1 pair) |
| 24 h | 0 | 124 (1 pair) |

Other observations:

- Senders by payload arrival: 52.4 percent arrive header-only, 40.4 percent with a payload, 7.2 percent both.
- MapReport packets on the `map` topic carry `position_precision` up to 32 bits (8.9 percent at 32); they count as positions at their own precision.
- The regional topic segments (`msh/US/MeshSTL`, `MO`, `IL`) carry radio envelopes only, so every via_mqtt copy arrives under some other part of `msh/US`.
