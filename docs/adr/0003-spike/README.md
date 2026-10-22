# Phase 1 spike for ADR 0003

Evidence for `docs/adr/0003-direct-link-and-position-source.md`: a 48-hour capture of the public Meshtastic broker, a decoder, and the measurements the plan's Phase 1 spike asks for. Nothing here is production code; the ingester is built separately in Phase 1.

## Broker and topics

From the Meshtastic docs (`https://meshtastic.org/docs/software/integrations/mqtt/` and `https://meshtastic.org/docs/configuration/module/mqtt/`): host `mqtt.meshtastic.org`, port 1883, username `meshdev`, password `large4cats`. Gateways publish protobuf `ServiceEnvelope` messages to `msh/REGION/2/e/CHANNELNAME/!gatewayid`; the default root for the United States is `msh/US`, and many communities insert a state or group segment (`msh/US/MO/2/e/...`, `msh/US/MeshSTL/2/e/...`). JSON copies go to `.../2/json/...`, map reports to `.../2/map/`, status strings to `.../2/stat/`.

The capture subscribes to `msh/US/#`. Measured over the first 3.5 hours: about 1.55 million messages and 270 MB of JSON lines per hour, about 13 GB for 48 hours. That is under the 20 GB line in the brief, so nothing was narrowed. Free space on C: was 36 GB on 2026-10-22.

## Files

- `capture.py`: subscribes, buffers, appends one JSON line per delivery (`received_at` in UTC from our clock, `topic`, `payload_b64`) to `capture-YYYY-MM-DDTHH.jsonl`, flushes every 60 seconds or 20 000 records, reconnects with backoff from 1 to 120 seconds. JSON lines rather than Parquet because an append-only text file survives a crash mid-hour with every flushed record readable, needs no writer state, and the landing format belongs to the ingester, not the spike. It decodes nothing and logs no payload or topic.
- `analyze.py`: tallies each hourly file in its own worker process, decrypts default-key packets (AES-128-CTR, key `1PG7OiApB1nwvP+rz05pAQ==`, nonce = packet id as 8 bytes little-endian, sender id as 4 bytes little-endian, 4 zero bytes), decodes Position, NodeInfo, Telemetry, NeighborInfo and MapReport, and prints measurements (a) to (c), the regional topics, the counts, (e) yield and funnel, a sensitivity table and the (f) check for each area. Aggregates only; no node id, name or coordinate is printed.
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

Check it is alive: one `flush` line per minute with `count`, `bytes`, `path`, `duration_s` and `last_message_age_s`, and one new file per hour.

```
docker ps --filter name=mesh-spike-capture
docker logs --since 5m mesh-spike-capture
ls -la C:/Users/Casey/mesh-terrain-data/spike-capture
```

Stop it. SIGTERM flushes the buffer first and the data stays on disk.

```
docker stop mesh-spike-capture
```

Run the analysis. The committed scripts are mounted over the image's copies, so the capture container's image never needs a rebuild. `MSYS_NO_PATHCONV=1` stops Git Bash from rewriting `/data` into a Windows path. About 90 seconds for 3.5 hours of data on 12 cores.

```
MSYS_NO_PATHCONV=1 docker run --rm \
  -v C:/Users/Casey/mesh-terrain-data/spike-capture:/data:ro \
  -v C:/Users/Casey/mesh-terrain-data:/bounds:ro \
  -v "$(pwd -W)/docs/adr/0003-spike:/spike:ro" \
  mesh-spike python /spike/analyze.py /data /bounds/msa_boundary_pr7.csv /bounds/states_mo_il.csv
```

For the final numbers, replace `/bounds/msa_boundary_pr7.csv` with the merged seed, for example by mounting `dbt/seeds/msa_boundary.csv`.

## Boundaries

All boundary files stay outside the repo in `C:/Users/Casey/mesh-terrain-data/`.

- MSA: the seed from PR #7 (`git show origin/feat/msa-boundary-seed:dbt/seeds/msa_boundary.csv`), saved as `msa_boundary_pr7.csv`, sha256 `711054e1cfc2962a26db8e6c4b385468d759ff81172fd905f57c60e916925aea`, the value warehouse-eng recorded. The WKT is split over two rows by `part`; `analyze.py` joins the chunks in `part` order.
- Missouri (GEOID 29) and Illinois (GEOID 17): `https://www2.census.gov/geo/tiger/TIGER2025/STATE/tl_2025_us_state.zip`, sha256 `59a220888a8d9be8117c4fcd38f542bd02d81abf0d198c78113595ad540dd957`, converted with pyshp 2.3.1 and shapely 2.2.0 in a throwaway `python:3.12-slim-bookworm` container, the same way the CBSA seed was made. Output `states_mo_il.csv` (columns `geoid`, `name`, `source`, `geometry_wkt`), sha256 `8c0749d467869d9a5a4b71898958b006edc78bf15b6984b1b0e997bfc43da45b`; both MultiPolygons valid.

## Rule definitions used for (e)

- (i) `relay_node`: the candidates are the senders the receiving gateway heard anywhere in the capture (the ADR proposes a trailing 24 hours for production). If the byte equals the sender's own low byte, the relayer is the sender. Otherwise the relayer is resolved when exactly one candidate has that low byte. `relay_node_strict` drops the own-byte shortcut.
- (ii) NeighborInfo: the candidates are the neighbours listed in the gateway's own NeighborInfo reports, matched the same way.
- (iii) combined: (i) or (ii), dropped when both resolve and disagree. Each receipt is one (gateway, sender, packet id), so duplicates count once.
- Then `lnk_03`, `lnk_02` (closest usable position in time for each end, within 30 minutes), `lnk_05` on the distance between the raw integer positions, and `lnk_04` on the (relayer, gateway, clock hour) group.

## Interim results not in the ADR (3.47 hours, weekly figures extrapolated x48.48)

Funnel, MSA, receipts:

| step | relay_node | neighbor_info | combined | relay_node_strict |
| --- | --- | --- | --- | --- |
| relayed receipts at area gateways | 243 | 243 | 243 | 243 |
| `lnk_01` relayer resolved | 180 | 0 | 180 | 175 |
| `lnk_03` different nodes | 180 | 0 | 180 | 175 |
| `lnk_02` both usable within 30 min | 11 | 0 | 11 | 10 |
| `lnk_05` 500 m to 60 km | 1 | 0 | 1 | 0 |

Missouri plus Illinois: 656, 436, 436, 15, 1 for the combined rule.

Sensitivity, combined rule, MSA (diagnostic only; these are not the contract rules):

| position window | min packets per hour | per week, 500 m | relayers | gateways | per week, 3 km |
| --- | --- | --- | --- | --- | --- |
| 30 min (contract) | 3 | 0 | 0 | 0 | 0 |
| 30 min | 1 | 48 | 1 | 1 | 48 |
| 24 h | 3 | 0 | 0 | 0 | 0 |
| 24 h | 1 | 145 | 1 | 1 | 145 |

Ignoring positions altogether, the MSA had 17 (relayer, gateway, hour) groups with 3 or more resolved receipts (about 820 a week) across 9 pairs, 8 relayers and 7 gateways; 1 of the 9 pairs has both ends ever at 15 bits or more. Missouri plus Illinois: 41 groups (about 1 980 a week), 21 pairs, 19 relayers, 18 gateways, again 1 pair with both ends usable. This came from an ad hoc run of the same functions, not from `analyze.py`.

Other observations:

- The broker removes the encrypted payload from 96.2 percent of envelopes and keeps the header. Whether a payload arrives follows the sender: 53.5 percent of senders arrive header-only, 39.3 percent with a payload, 7.2 percent both. Of the packets that arrive with a payload, 75.5 percent carry the `OK_TO_MQTT` bit; plaintext uplinks, which the broker cannot inspect, mostly do not.
- MapReport packets on the `map` topic carry `position_precision` up to 32 bits (9.0 percent at 32); they count as positions in the analysis at their own precision.
