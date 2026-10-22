# Phase 1 spike for ADR 0003

Evidence for `docs/adr/0003-direct-link-and-position-source.md`. A 48-hour capture of the public Meshtastic broker, a decoder, and the measurements the plan's Phase 1 spike asks for. Nothing here is production code; the ingester is built separately in Phase 1.

## Broker and topics

From the Meshtastic docs (`https://meshtastic.org/docs/software/integrations/mqtt/` and `https://meshtastic.org/docs/configuration/module/mqtt/`): host `mqtt.meshtastic.org`, port 1883, username `meshdev`, password `large4cats`. Gateways publish protobuf `ServiceEnvelope` messages to `msh/REGION/2/e/CHANNELNAME/!gatewayid`; the default root for the United States is `msh/US`, and many communities insert a state or group segment (`msh/US/MO/2/e/...`, `msh/US/MeshSTL/...`). JSON copies go to `.../2/json/...`, map reports to `.../2/map/`, and online status strings to `.../2/stat/`.

The capture subscribes to `msh/US/#`, which covers every one of those. Measured in the first five minutes: about 400 messages per second, 262 MB per hour of JSON lines, about 12.6 GB for 48 hours. That is under the 20 GB line in the brief, so nothing was narrowed. Free space on the host drive at the start was 38 GB.

## Files

- `capture.py`: subscribes, buffers, appends one JSON line per delivery (`received_at` in UTC from our clock, `topic`, `payload_b64`) to `capture-YYYY-MM-DDTHH.jsonl`, flushes every 60 seconds or 20000 records, reconnects with backoff (1 to 120 seconds). JSON lines rather than Parquet because an append-only text file survives a crash mid-hour with every flushed record readable, needs no writer state, and the production format decision belongs to the ingester, not the spike. It decodes nothing and logs no payload or topic.
- `analyze.py`: parses every envelope, decrypts default-key packets (AES-128-CTR, key `1PG7OiApB1nwvP+rz05pAQ==`, nonce = packet id as 8 bytes little-endian, sender id as 4 bytes little-endian, 4 zero bytes), decodes Position, NodeInfo, Telemetry, NeighborInfo and MapReport, and prints the (a), (b), (c) tables, the MSA counts and the link-hour estimate. Aggregates only; no node id or coordinate is printed.
- `Dockerfile`: one image for both scripts, pinned spike-only dependencies.

## Commands

Build the image (from the repository root):

```
docker build -t mesh-spike docs/adr/0003-spike
```

Start the capture (detached, restarts with Docker, data on the host outside the repo):

```
mkdir -p C:/Users/Casey/mesh-terrain-data/spike-capture
docker run -d --name mesh-spike-capture --restart unless-stopped \
  -v C:/Users/Casey/mesh-terrain-data/spike-capture:/data \
  -e MQTT_HOST=mqtt.meshtastic.org -e MQTT_PORT=1883 \
  -e MQTT_USER=meshdev -e MQTT_PASSWORD=large4cats \
  -e "MQTT_TOPICS=msh/US/#" mesh-spike
```

Check it is alive (one `flush` line per minute with `count`, `bytes`, `path`, `duration_s` and `last_message_age_s`; the hourly file list should grow by one file per hour):

```
docker ps --filter name=mesh-spike-capture
docker logs --since 5m mesh-spike-capture
ls -la C:/Users/Casey/mesh-terrain-data/spike-capture
```

Stop it (SIGTERM flushes the buffer first; the data stays on disk):

```
docker stop mesh-spike-capture
docker rm mesh-spike-capture
```

Run the analysis. The MSA polygon is the CBSA 41180 boundary in the CSV shape agreed with warehouse-eng (`geoid`, `name`, `geometry_wkt`). Until `dbt/seeds/msa_boundary.csv` lands, the spike reads a copy fetched from TIGERweb into the data directory (see "MSA polygon"). From Git Bash the `MSYS_NO_PATHCONV=1` prefix stops the shell rewriting `/data` into a Windows path.

```
MSYS_NO_PATHCONV=1 docker run --rm \
  -v C:/Users/Casey/mesh-terrain-data/spike-capture:/data:ro \
  -v C:/Users/Casey/mesh-terrain-data/msa_boundary.csv:/opt/spike/msa_boundary.csv:ro \
  mesh-spike python analyze.py /data /opt/spike/msa_boundary.csv
```

## MSA polygon

`dbt/seeds/msa_boundary.csv` did not exist on any branch when the spike ran. The polygon was fetched from the Census TIGERweb REST service, layer 3 (Metropolitan Statistical Areas) of `https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/CBSA/MapServer`, query `GEOID='41180'`, `outSR=4326`, `f=geojson`, and written as a one-row CSV with the agreed columns:

```
curl -s "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/CBSA/MapServer/3/query?where=GEOID%3D%2741180%27&outFields=GEOID,NAME&outSR=4326&f=geojson" \
  -o C:/Users/Casey/mesh-terrain-data/msa_41180.geojson
```

followed by a shapely conversion to a MultiPolygon WKT. Bounds: 91.42 W to 89.14 W, 38.00 N to 39.52 N. When the seed merges, point the second mount at `dbt/seeds/msa_boundary.csv` instead; the script reads the same columns.

## What the first minutes showed

- The broker forwards the `MeshPacket` header of nearly every default-key packet (sender, id, `hop_start`, `hop_limit` rewritten to 0, `rx_snr`, `rx_rssi`, `relay_node`, `via_mqtt`) but removes the encrypted payload from about 96 percent of them. The removal follows the sender, not the portnum: a sender's packets arrive either all stripped or all with a payload. Packets that arrive with a payload carry the `OK_TO_MQTT` bit in `Data.bitfield` 92 percent of the time; plaintext uplinks, which the broker cannot inspect, mostly do not. The docs describe this only as "traffic restrictions ... applied at the network level". Consequence: link measurements (`rx_snr`, `rx_rssi`, `relay_node`) exist for every packet, but positions exist only for nodes whose owners opted in.
- The decode status set from the contract (`ing_03`) has no value for "payload removed by the broker"; the analysis reports it as `decrypt_failed` with reason `stripped`.
- The low byte of `relay_node` matched exactly one sender heard by the same gateway in about 42 percent of relayed packets, more than one in 27 percent, and none in 32 percent; against all senders in the capture it always matched many. The candidate set for `lnk_01` therefore has to be scoped per gateway.

Interim numbers are in the ADR and are replaced at 48 hours.
