# Phase 1 spike for ADR 0003

Evidence for `docs/adr/0003-direct-link-and-position-source.md`: a 48-hour capture of the public Meshtastic broker, a 24-hour capture of the MeshSTL broker, a decoder, and the measurements the plan's Phase 1 spike and its addendum ask for. Nothing here is production code; the ingester is built separately in Phase 1.

## Brokers and topics

Public broker, from the Meshtastic docs (`https://meshtastic.org/docs/software/integrations/mqtt/` and `https://meshtastic.org/docs/configuration/module/mqtt/`): host `mqtt.meshtastic.org`, port 1883, username `meshdev`, password `large4cats`. Gateways publish protobuf `ServiceEnvelope` messages to `msh/REGION/2/e/CHANNELNAME/!gatewayid`. The default root for the United States is `msh/US`, and many communities insert a state or group segment (`msh/US/MO/2/e/...`, `msh/US/MeshSTL/2/e/...`). JSON copies go to `.../2/json/...`, map reports to `.../2/map/`, and status strings to `.../2/stat/`. The capture subscribes to `msh/US/#`. Measured over the first 4.4 hours: about 1.5 million messages and 270 MB of JSON lines an hour, about 13 GB for 48 hours.

MeshSTL broker, from `https://meshstl.org` (section `#mqtt-settings` for the server and credentials, `#channels` for the channel keys): host `mqtt.meshstl.org`, port 1883, no TLS, root topic `msh/US/MeshSTL`. The username and password are posted on that page and are not copied here or anywhere in the repo; the container reads them from `C:/Users/Casey/mesh-terrain-data/meshstl.env` (outside the repo). The MeshSTL channel key on the same page is never used: the analysis decrypts the default key only, so MeshSTL-channel packets show as `decrypt_failed: wrong_key`.

## Files

- `capture.py`: subscribes, buffers, and appends one JSON line per delivery (`received_at` in UTC from our clock, `topic`, `payload_b64`) to `capture-YYYY-MM-DDTHH.jsonl`. It flushes every 60 seconds or 20 000 records and reconnects with backoff from 1 to 120 seconds. JSON lines rather than Parquet: an append-only text file survives a crash mid-hour with every flushed record readable, needs no writer state, and the landing format belongs to the ingester. It never logs a payload or a topic. With `CAPTURE_THIN=1` it writes headers only (parsed header fields, decode status, payload length, no bytes); that is the thinning rule below and is never the default.
- `analyze.py`: tallies each hourly file in its own worker process, decrypts default-key packets (AES-128-CTR, key `1PG7OiApB1nwvP+rz05pAQ==`, nonce = packet id as 8 bytes little-endian, sender id as 4 bytes little-endian, 4 zero bytes), decodes Position, NodeInfo, Telemetry, NeighborInfo and MapReport, and prints every table below. It reads thin records too. `--start` and `--end` (ISO 8601 UTC) restrict it to a window, which is how the two brokers are compared on the same hours. Aggregates only; no node id, name or coordinate is printed.
- `Dockerfile`: one image for both scripts, with spike-only pins.

## Commands

Build the image and start the public capture (from the repository root, Git Bash):

```
docker build -t mesh-spike docs/adr/0003-spike
mkdir -p C:/Users/Casey/mesh-terrain-data/spike-capture
docker run -d --name mesh-spike-capture --restart unless-stopped \
  -v C:/Users/Casey/mesh-terrain-data/spike-capture:/data \
  -e MQTT_HOST=mqtt.meshtastic.org -e MQTT_PORT=1883 \
  -e MQTT_USER=meshdev -e MQTT_PASSWORD=large4cats \
  -e "MQTT_TOPICS=msh/US/#" mesh-spike
```

Start the MeshSTL capture (only when the lead says Casey has posted his courtesy note). The env file holds `MQTT_HOST`, `MQTT_PORT`, `MQTT_USER`, `MQTT_PASSWORD`, `MQTT_TOPICS=msh/US/MeshSTL/#` and `DATA_DIR=/data`:

```
mkdir -p C:/Users/Casey/mesh-terrain-data/meshstl-capture
docker run -d --name mesh-meshstl-capture --restart unless-stopped \
  --env-file C:/Users/Casey/mesh-terrain-data/meshstl.env \
  -v C:/Users/Casey/mesh-terrain-data/meshstl-capture:/data mesh-spike
```

Ten-minute rate check, after the container has run ten minutes. The `total` field of the last flush line divided by the minutes elapsed is the message rate; the file size divided by the minutes elapsed times 1 440 is the daily volume:

```
docker logs mesh-meshstl-capture 2>&1 | grep '"flush"' | tail -1
ls -la C:/Users/Casey/mesh-terrain-data/meshstl-capture
```

Disk check and the thinning rule. Free space on C: was 34 GB at 20:29Z on 2026-10-22, the public capture was at 1.2 GB after 4.3 hours and runs about 270 MB an hour, so about 12 GB more by its end. Both captures together must stay under the 20 GB line. If the MeshSTL ten-minute projection plus 12 GB passes 20 GB, do not skip MeshSTL: restart the public capture in headers-only mode for its remaining hours and record the time in `docs/STATUS.md`. The thin file keeps the header fields and decode status, so every link measurement still works; positions are lost for those hours.

```
df -h /c
du -sh C:/Users/Casey/mesh-terrain-data/spike-capture C:/Users/Casey/mesh-terrain-data/meshstl-capture
docker stop mesh-spike-capture && docker rm mesh-spike-capture
docker run -d --name mesh-spike-capture --restart unless-stopped \
  -v C:/Users/Casey/mesh-terrain-data/spike-capture:/data \
  -e MQTT_HOST=mqtt.meshtastic.org -e MQTT_PORT=1883 \
  -e MQTT_USER=meshdev -e MQTT_PASSWORD=large4cats \
  -e "MQTT_TOPICS=msh/US/#" -e CAPTURE_THIN=1 mesh-spike
```

Check either capture is alive: one `flush` line a minute with `count`, `bytes`, `path`, `duration_s` and `last_message_age_s`, and one new file an hour.

```
docker ps --filter name=mesh-
docker logs --since 5m mesh-spike-capture
docker logs --since 5m mesh-meshstl-capture
```

Stop a capture. SIGTERM flushes the buffer first, and the data stays on disk.

```
docker stop mesh-spike-capture
docker stop mesh-meshstl-capture
```

Run the analysis. The committed scripts are mounted over the image's copies, so the image a capture runs from never needs a rebuild. `MSYS_NO_PATHCONV=1` stops Git Bash from rewriting `/data` into a Windows path. About 70 seconds for 4 hours of public data on 12 cores.

```
MSYS_NO_PATHCONV=1 docker run --rm \
  -v C:/Users/Casey/mesh-terrain-data/spike-capture:/data:ro \
  -v C:/Users/Casey/mesh-terrain-data:/bounds:ro \
  -v "$(pwd -W)/dbt/seeds:/seeds:ro" \
  -v "$(pwd -W)/docs/adr/0003-spike:/spike:ro" \
  mesh-spike python /spike/analyze.py /data /seeds/msa_boundary.csv /bounds/states_mo_il.csv
```

The MeshSTL capture: the same command with `meshstl-capture` as `/data`. To compare the two brokers on the same hours, add `--start` and `--end` with the MeshSTL window to the public run.

## Boundaries

- MSA: the merged seed `dbt/seeds/msa_boundary.csv`, sha256 `711054e1cfc2962a26db8e6c4b385468d759ff81172fd905f57c60e916925aea`, the value warehouse-eng recorded in PR #7. The seed splits the WKT over two rows by `part`, and `analyze.py` joins the chunks in `part` order. The view `staging.stg_msa_boundary` gives the same polygon, but the spike container does not connect to the warehouse.
- Missouri (GEOID 29) and Illinois (GEOID 17) come from `https://www2.census.gov/geo/tiger/TIGER2025/STATE/tl_2025_us_state.zip`, sha256 `59a220888a8d9be8117c4fcd38f542bd02d81abf0d198c78113595ad540dd957`, converted with pyshp 2.3.1 and shapely 2.2.0 in a throwaway `python:3.12-slim-bookworm` container, the same way the CBSA seed was made. The output is `C:/Users/Casey/mesh-terrain-data/states_mo_il.csv` (columns `geoid`, `name`, `source`, `geometry_wkt`), sha256 `8c0749d467869d9a5a4b71898958b006edc78bf15b6984b1b0e997bfc43da45b`. Both MultiPolygons are valid. The file stays outside the repo.

## Definitions

- Radio delivery: an envelope on an `e` topic that parses, has a sender, and has `via_mqtt` false. The reviewer's 82 812 is the radio deliveries in the file `capture-2026-10-22T18.jsonl` alone, the gateway's own packets included; `analyze.py` reproduces it on that file (53 406 without the gateway's own). The 271 208 at 3.68 hours is the same definition over 3.68 hours.
- Radio receipt: a distinct (gateway, sender, packet id) among radio deliveries, the gateway's own packets excluded. Every link figure uses the radio receipts that carry `relay_node`. Candidates for the relayer are the senders the receiving gateway heard by radio anywhere in the capture; the ADR proposes the trailing 24 hours for production.
- `lnk_06`: `rx_rssi` set and in [-140, 0] dBm, `rx_snr` in [-25, 15] dB.
- (i) `relay_node`: if the byte equals the sender's own low byte, the relayer is the sender. Otherwise it is the one candidate with that low byte, or unresolved when there are none or several. `relay_node_strict` drops the own-byte shortcut.
- (ii) NeighborInfo: the candidates are the neighbours listed in the gateway's own NeighborInfo reports, matched the same way.
- (iii) combined: (i) or (ii), dropped when both resolve and disagree.
- `lnk_02`, 30-minute rule: the usable position (15 bits or more) closest in time for each end, within 30 minutes. A failure is attributed to the relayer first, then the gateway, under: no position in the capture, a position only outside the area, below 15 bits, or usable but outside the window.
- Stationarity rule (addendum 2b): a node whose every position in the capture is one box (same quantised latitude, longitude and bits) gets that box for every receipt; other nodes fall back to the 30-minute rule. The capture window stands in for the 7-day window production would use.
- Gateway-only fallback: the relayer is resolved and the gateway is positioned; no relayer position and no distance. The 500 m and 3 km columns do not apply.
- Overlapping boxes (addendum 2c): two position boxes that share ground have no single distance, only a range from 0 m to the larger box's diagonal (926 m at 16 bits, 1 852 m at 15 bits). They get their own funnel row and never a link-hour.
- `lnk_04` and the 3 km test: at least 3 receipts in the (relayer, gateway, clock hour) group, tested on the median distance of the group.

## Results

The measurement tables are in `results.md` beside this file, interim until the captures end.
