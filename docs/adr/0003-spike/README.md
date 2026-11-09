# Phase 1 spike for ADR 0003

Evidence for `docs/adr/0003-direct-link-and-position-source.md`: a capture of the public Meshtastic broker (planned for 48 hours, observed 63.594 hours because the container had no end time), a 24-hour capture of the MeshSTL broker, a decoder, and the measurements the plan's Phase 1 spike and its addendum ask for. Nothing here is production code; the ingester is built separately in Phase 1.

## Brokers and topics

Public broker, from the Meshtastic docs (`https://meshtastic.org/docs/software/integrations/mqtt/` and `https://meshtastic.org/docs/configuration/module/mqtt/`): host `mqtt.meshtastic.org`, port 1883, username `meshdev`, password `large4cats`. Gateways publish protobuf `ServiceEnvelope` messages to `msh/REGION/2/e/CHANNELNAME/!gatewayid`. The default root for the United States is `msh/US`, and many communities insert a state or group segment (`msh/US/MO/2/e/...`, `msh/US/MeshSTL/2/e/...`). JSON copies go to `.../2/json/...`, map reports to `.../2/map/`, and status strings to `.../2/stat/`. The capture subscribes to `msh/US/#`. Measured over 63.594 observed hours: 1.28 million messages and 232 MB of JSON lines an hour, 5.57 GB a day, 88.6 percent of it header-only via_mqtt copies. The hourly files were gzipped in place after the capture (64 files, 2.740 GB) and are read directly; a verified zstd Parquet copy of each hour sits in `C:/Users/Casey/mesh-terrain-data/spike-parquet/` (columns `received_at`, `topic`, `payload` as bytes).

MeshSTL broker, from `https://meshstl.org` (section `#mqtt-settings` for the server and credentials, `#channels` for the channel keys): host `mqtt.meshstl.org`, port 1883, no TLS, root topic `msh/US/MeshSTL`. The username and password are posted on that page and are not copied here or anywhere in the repo; the container reads them from `C:/Users/Casey/mesh-terrain-data/meshstl.env` (outside the repo). The MeshSTL channel key on the same page is never used: the analysis decrypts the default key only, so MeshSTL-channel packets show as `decrypt_failed: wrong_key`.

## Files

- `capture.py`: the on-demand capture command of ADR 0005, brought to that design in place (below). During the spike it was a raw JSON lines capture; that version is in git history and is not to be run.
- `analyze.py`: tallies each hourly file (`.jsonl` or `.jsonl.gz`) in its own worker process, decrypts default-key packets (AES-128-CTR, key `1PG7OiApB1nwvP+rz05pAQ==`, nonce = packet id as 8 bytes little-endian, sender id as 4 bytes little-endian, 4 zero bytes), decodes Position, NodeInfo, Telemetry, NeighborInfo and MapReport, and prints every table in `results.md`. It reads thin records too. `--start` and `--end` (ISO 8601 UTC) restrict it to a window, which is how the two brokers are compared on the same hours. `--roots MO,IL,MeshSTL` keeps only topics under those roots, which is what a subscription to those prefixes would have received. `--positions-from DIR` adds another capture's positions (same window) so a relayer silent on one broker can be placed from the other. Aggregates only; no node id, name or coordinate is printed.
- Denominators: every rate divides by observed hours, the sum of the capture's segments. A segment is a run of records with no gap over 15 minutes; a gap is not observed time. Weekly figures are weighted: the weekday hourly rate times 120 plus the weekend hourly rate times 48, with weekday and weekend taken as local days (CDT, UTC minus 5, for the whole capture). The flat figure (overall hourly rate times 168) is printed beside it.
- `Dockerfile`: the spike analysis image, with spike-only pins. The capture command runs from the repository with uv, not in this image.
- `reproduce_from_snapshot.py`: the reproduction gate (below).

## The capture command

`capture.py` is the project's on-demand capture command (ADR 0005 item 1). One run connects to one broker, keeps only the ADR 0005 allowlist with HMAC pseudonyms in place of node ids, and writes one snapshot: zstd Parquet by table and UTC hour, `coverage.json`, a README and a sha256 manifest. Nothing raw lands: no payload bytes, no text, no names, no real id. It runs only when started, under no restart policy and no schedule, and stops itself.

Parameters:

| parameter | meaning |
| --- | --- |
| `--host`, `--port` | the broker; one per run |
| `--source` | `public` or `meshstl`: which broker flag (`seen_public`, `seen_meshstl`) the rows carry |
| `--topic` | a topic filter; repeat for several. The documented default for the public broker is `msh/US/MeshSTL/#` and `msh/US/MO/#` (ADR 0005 item 7) |
| `--end-at` | END_AT, ISO 8601 with an offset; nothing received at or after it is kept |
| `--max-raw-bytes` | MAX_RAW_BYTES, default 10 000 000 000, measured on the snapshot folder at each check |
| `--snapshot-id` | the new snapshot's id; a refresh is always a new id |
| `--meshstl-approved-by-casey` | the date of Casey's approval; the command refuses the MeshSTL broker without it, and the manifest records it |

`NODE_ID_HMAC_KEY` (32 random bytes written as 64 hex characters, in `.env`) must be set; the command refuses to start without it. `SNAPSHOTS_DIR` defaults to `C:/Users/Casey/mesh-terrain-data/snapshots`. The public broker's credentials are Meshtastic's published ones and are the defaults; for the MeshSTL broker, export `MQTT_USER` and `MQTT_PASSWORD` from `C:/Users/Casey/mesh-terrain-data/meshstl.env`, which is outside the repo.

How a run ends. The run writes into `<snapshots>/<id>.partial/`, flushing one file per table and hour every 5 minutes or 5 000 deliveries. At END_AT, at MAX_RAW_BYTES, or on Ctrl-C or SIGTERM, it merges each hour's flush files into one file, writes the README and the manifest with the stop reason (`end_at`, `max_raw_bytes` or `signal`) and the byte count, renames the folder to `<id>/` and exits 0. A write that fails twice exits non-zero and leaves `<id>.partial/` without a manifest; so does a crash or a power loss. Nothing deletes a `.partial` folder: the run report lists it, Casey deletes or keeps it by hand, and a new run with the same id refuses while it exists.

Worked example: the 2026-10-22 public run as this command would start it. The spike ran from the 2026-10-22T16Z hour with no end time until it stopped at 2026-10-25T07:43Z, 63.6 observed hours; with the parameters it would have ended at its planned 48 hours, with MAX_RAW_BYTES above the README's 48-hour estimate of 11.1 GB of JSON lines as the guard:

```
uv run --frozen python -I docs/adr/0003-spike/capture.py \
  --host mqtt.meshtastic.org --source public --topic 'msh/US/#' \
  --end-at 2026-10-24T16:00:00+00:00 --max-raw-bytes 12000000000 \
  --snapshot-id meshtastic-capture-2026-10-22
```

That run would have written `C:/Users/Casey/mesh-terrain-data/snapshots/meshtastic-capture-2026-10-22.partial/`, stopped with `end_at` (allowlisted Parquet of `msh/US/#` is far below 12 GB in 48 hours), and renamed it to `meshtastic-capture-2026-10-22/`. The actual snapshot of that id is the reduction of the spike's two captures (`python -I -m ingester.reduce`), since they ran before this command existed; a new run today takes a new id and the documented topics:

```
uv run --frozen python -I docs/adr/0003-spike/capture.py \
  --host mqtt.meshtastic.org --source public \
  --topic 'msh/US/MeshSTL/#' --topic 'msh/US/MO/#' \
  --end-at <YYYY-MM-DDTHH:MM:SS+00:00> --snapshot-id meshtastic-capture-<YYYY-MM-DD>
```

The MeshSTL broker, only after Casey has approved this run (the MeshSTL capture of 2026-10-23 ran its agreed 24 hours from 22:09Z):

```
set -a; source C:/Users/Casey/mesh-terrain-data/meshstl.env; set +a
uv run --frozen python -I docs/adr/0003-spike/capture.py \
  --host mqtt.meshstl.org --source meshstl --topic 'msh/US/MeshSTL/#' \
  --end-at <YYYY-MM-DDTHH:MM:SS+00:00> --snapshot-id meshstl-capture-<YYYY-MM-DD> \
  --meshstl-approved-by-casey <YYYY-MM-DD>
```

Do not run any capture without the lead's brief, and never the MeshSTL one without Casey's approval for that run. A make target that wraps this command is requested from platform-eng.

## Reproduction gate

`reproduce_from_snapshot.py` reruns every ADR 0003 link figure from the snapshot alone, checking every file against the manifest first and never opening the real-id archive. Pseudonyms become stand-in integers that keep the stored low byte, so `analyze.py` runs unchanged on the rows:

```
uv run --frozen --with shapely==2.2.0 --with pyproj==3.8.0 python -I \
  docs/adr/0003-spike/reproduce_from_snapshot.py \
  C:/Users/Casey/mesh-terrain-data/snapshots/meshtastic-capture-2026-10-22 <out-dir>
```

It writes the seven runs below and `gate.md`, the comparison with `results.md` including `merged_cross_broker` and `merged_repeat` per broker, and exits 1 if any figure differs.

## Rerunning the spike analysis

The spike captures are finished and their capture recipe, which used a restart policy ADR 0005 forbids, is gone; the analysis of the raw archive is kept here while the archive exists.

Run the analysis. The committed scripts are mounted at `/spike` and run from there instead of the image's copies in `/opt/spike`, so the image never needs a rebuild. `MSYS_NO_PATHCONV=1` stops Git Bash from rewriting `/data` into a Windows path. It reads the gzipped hours directly.

```
MSYS_NO_PATHCONV=1 docker run --rm \
  -v C:/Users/Casey/mesh-terrain-data/spike-capture:/data:ro \
  -v C:/Users/Casey/mesh-terrain-data:/bounds:ro \
  -v "$(pwd -W)/dbt/seeds:/seeds:ro" \
  -v "$(pwd -W)/docs/adr/0003-spike:/spike:ro" \
  mesh-spike python /spike/analyze.py /data /seeds/msa_boundary.csv /bounds/states_mo_il.csv
```

The seven runs behind `results.md`, all with the command above plus:

| run | `/data` | extra arguments |
| --- | --- | --- |
| public, full capture | `spike-capture` | none |
| public, regional roots only | `spike-capture` | `--roots MO,IL,MeshSTL` |
| public, the roots ADR 0005 subscribes | `spike-capture` | `--roots MO,MeshSTL` (MSA only: omit the states file) |
| public, shared 24 hours | `spike-capture` | `--start 2026-10-23T22:08:59+00:00 --end 2026-10-24T22:08:59+00:00` |
| MeshSTL, its 24 hours | `meshstl-capture` | the same `--start` and `--end` |
| public with MeshSTL positions | `spike-capture` | the same window plus `--positions-from /bounds/meshstl-capture` |
| MeshSTL with public positions | `meshstl-capture` | the same window plus `--positions-from /bounds/spike-capture` |

## Boundaries

- MSA: the merged seed `dbt/seeds/msa_boundary.csv`, sha256 `711054e1cfc2962a26db8e6c4b385468d759ff81172fd905f57c60e916925aea`, the value warehouse-eng recorded in PR #7. The seed splits the WKT over two rows by `part`, and `analyze.py` joins the chunks in `part` order. The view `staging.stg_msa_boundary` gives the same polygon, but the spike container does not connect to the warehouse.
- Missouri (GEOID 29) and Illinois (GEOID 17) come from `https://www2.census.gov/geo/tiger/TIGER2025/STATE/tl_2025_us_state.zip`, sha256 `59a220888a8d9be8117c4fcd38f542bd02d81abf0d198c78113595ad540dd957`, converted with pyshp 2.3.1 and shapely 2.2.0 in a throwaway `python:3.12-slim-bookworm` container, the same way the CBSA seed was made. The output is `C:/Users/Casey/mesh-terrain-data/states_mo_il.csv` (columns `geoid`, `name`, `source`, `geometry_wkt`), sha256 `8c0749d467869d9a5a4b71898958b006edc78bf15b6984b1b0e997bfc43da45b`. Both MultiPolygons are valid. The file stays outside the repo.

## Definitions

- Radio delivery: an envelope on an `e` topic that parses, has a sender, and has `via_mqtt` false. The reviewer's 82 812 is the radio deliveries in the hour 2026-10-22T18Z (`capture-2026-10-22T18.jsonl.gz`), the gateway's own packets included; the hourly table in `results.md` prints it. The 271 208 at 3.68 hours is the same definition over 3.68 hours.
- Radio receipt: a distinct (gateway, sender, packet id) among radio deliveries, the gateway's own packets excluded. Every link figure uses the radio receipts that carry `relay_node`. Candidates for the relayer are the senders the receiving gateway heard by radio anywhere in the capture, including after the receipt, so the measurement looks ahead; ADR 0003 adopts the trailing 24 hours for production (contract `lnk_01`).
- `lnk_06`: `rx_rssi` set and in [-140, 0] dBm, `rx_snr` in [-25, 15] dB.
- (i) `relay_node`: if the byte equals the sender's own low byte, the relayer is the sender. Otherwise it is the one candidate with that low byte, or unresolved when there are none or several. `relay_node_strict` drops the own-byte shortcut.
- (ii) NeighborInfo: the candidates are the neighbours listed in the gateway's own NeighborInfo reports, matched the same way.
- (iii) combined: (i) or (ii), dropped when both resolve and disagree.
- `lnk_02`, 30-minute rule: the usable position (15 bits or more) closest in time for each end, within 30 minutes. A failure is attributed to the relayer first, then the gateway, under: no position in the capture, a position only outside the area, below 15 bits, or usable but outside the window.
- Stationarity rule (addendum 2b): a node whose every position in the capture is one box (same quantised latitude, longitude and bits) gets that box for every receipt; other nodes fall back to the 30-minute rule. The capture window stands in for the trailing 7-day window production would use; unlike it, the capture includes positions sent after the receipt.
- Gateway-only fallback: the relayer is resolved and the gateway is positioned; no relayer position and no distance. The 500 m and 3 km columns do not apply.
- Overlapping boxes (addendum 2c): two position boxes that share ground have no single distance, only a range from 0 m to the larger box's diagonal (926 m at 16 bits, 1 852 m at 15 bits). They get their own funnel row and never a link-hour.
- `lnk_04` and the 3 km test: at least 3 receipts in the (relayer, gateway, clock hour) group, tested on the median distance of the group.

## Results

The measurement tables are in `results.md` beside this file. They are final.
