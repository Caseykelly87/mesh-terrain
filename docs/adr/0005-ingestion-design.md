# ADR 0005: Capture on demand under a byte cap, reduce to allowlisted and pseudonymised fields as hourly Parquet snapshots, and subscribe to the MeshSTL and MO roots

Date: 2026-10-30, open questions decided 2026-10-31, rescoped 2026-11-08 by Casey
Status: accepted
Proposed by: ingest-eng
Decided by: Casey

## Context

The observed data is a single capture from 2026-10-22 to 2026-10-25. The ADR 0003 spike captured the public broker for 63.594 observed hours and the MeshSTL broker for 24, landing every MQTT delivery with its payload bytes as the plan's Phase 1 design described (`docs/PLAN.md`, Phase 1). It showed three things the plan did not expect: most of the volume is copies with nothing to decode, the feed carries people's text messages and real node ids that would land on disk as they are, and a capture with no stopping rule keeps running and restarts itself. Casey decided eight questions on 2026-10-30 and 2026-10-31; on 2026-11-08 he rescoped the project to batch analysis on dated, immutable snapshots, with no continuous or scheduled ingestion now or planned. This ADR records the decisions as they stand after the rescope.

Terms:

- delivery: one MQTT message from a broker. On an `e` topic (`msh/US/MeshSTL/2/e/LongFast/!gatewayid`) it is a `ServiceEnvelope` holding one radio packet and the id of the gateway that uploaded it; ADR 0003 calls it an envelope. Brokers also publish `json`, `map` and `stat` topics.
- internet copy: a delivery with the header flag `via_mqtt` set, meaning the gateway got the packet over the internet, not by radio. On the public broker all but 1 204 of 74 314 521 are header-only: the broker removed the payload.
- radio receipt: a distinct (gateway, sender, packet id) among deliveries that parse, have a sender and have `via_mqtt` false, the gateway's own packets excluded. ADR 0003's link figures use only these.
- topic root: the segment after `msh/US` in a topic: `MeshSTL`, `MO`, `IL`, or `2` when the gateway sets none.
- HMAC: a keyed hash. HMAC-SHA-256(key, id) gives the same output for the same id and key; without the key nobody can compute or reverse it.
- pseudonym: the HMAC of a node id, stored in place of the id.
- modem preset: the radio setting a channel uses (bandwidth, spreading factor, coding rate), such as LongFast.
- snapshot: a dated, immutable input with an id, a capture date, a source URL, a licence and a sha256 manifest of its files. A refresh is a new snapshot id, never an overwrite. Terrain inputs (DEM tiles, land cover) are snapshots too; this ADR covers the MQTT one.
- capture run: one start of the capture command with its parameters, producing one snapshot.
- reduction: the batch step that turns captured deliveries into allowlisted, pseudonymised rows and count tables.

Evidence, uncompressed JSON lines as the spike stored them, payloads in base64 (`docs/adr/0003-spike/results.md`, "Volume"). Public rows are the full capture, 63.594 observed hours; the last row is the MeshSTL broker over its 24 hours.

| what | records | GB per day | share of bytes |
| --- | --- | --- | --- |
| public, everything under `msh/US/#` | 81 338 604 | 5.568 | 100.0% |
| public, internet copies (via_mqtt, header-only) | 74 314 521 | 4.936 | 88.6% |
| public, radio deliveries | 4 262 147 | 0.376 | 6.7% |
| public, roots MO, IL and MeshSTL only | 158 099 | 0.017 | |
| public, roots MO and MeshSTL only | 116 260 | 0.013 | |
| MeshSTL broker | 23 946 | 0.006 | |

The full public capture was 14.755 GB, 232 MB an hour. Under the regional roots there were 9 internet copies, all under `msh/US/IL`; none under MO or MeshSTL, and the MeshSTL broker sends none (results.md).

Parquet compression of the spike's raw records (results.md, "Volume"): the 64 gzipped public hours of the main segment, 2026-10-22T16 to 2026-10-25T07, 63.527 observed hours, each converted on its own to zstd Parquet with pyarrow 21.0.0 (columns `received_at` as a UTC timestamp, `topic`, `payload` as bytes), then read back and checked against its gzip file on row count and an order-preserving sha256; all 64 match. That is 81 251 409 rows; the 2 lines not converted are truncated JSON at the 07:43Z stop.

| form | GB | GB per day |
| --- | --- | --- |
| JSON lines | 14.728 | 5.564 |
| gzip | 2.740 | 1.035 |
| Parquet, zstd | 0.583 | 0.220 |

JSON lines to Parquet is 25.27 times smaller, JSON lines to gzip 5.37 times, gzip to Parquet 4.70 times; one hour of Parquet is 7.0 to 13.9 MB. These rows keep the payload bytes, the spike's shape, so they are a rough guide to the allowlisted rows of decision 4, not a measurement of them.

Text messages (results.md): 142 003 decoded TEXT_MESSAGE_APP packets on the public broker over 63.594 observed hours, and 38 on the MeshSTL broker over its 24 hours. Restarts: the spike containers ran past their windows under `--restart unless-stopped`, and Docker Desktop restarted them on 2026-10-28 (ADR 0003, Consequences).

Subscription scope (results.md, "Subscription scope"). Each row counts the radio receipts carrying `relay_node` at gateways positioned in the MSA that arrived under that root, and the link-hours kept when only those receipts are counted. Gateway-only link-hours have the relayer resolved and the gateway positioned; positions-ignored is ADR 0003's diagnostic with no position test.

| topic root | relayed metro receipts | share | gateway-only link-hours kept | share | positions-ignored link-hours kept | share |
| --- | --- | --- | --- | --- | --- | --- |
| `msh/US/MeshSTL` | 5 479 | 95.1% | 316 of 320 | 98.8% | 443 of 447 | 99.1% |
| `msh/US/MO` | 130 | 2.3% | 4 of 320 | 1.2% | 4 of 447 | 0.9% |
| `msh/US/IL` | 0 | 0.0% | 0 of 320 | 0.0% | 0 of 447 | 0.0% |
| `msh/US/2` (default root) | 151 | 2.6% | not counted alone | | not counted alone | |
| MO, IL and MeshSTL | 5 609 | 97.4% | 320 of 320 | 100.0% | 447 of 447 | 100.0% |

MO, IL and MeshSTL together keep everything in those two rows, so `msh/US/2` receipts add no link-hour there.

Simulated subscriptions (`analyze.py --roots`, so positions and relayer candidates also come only from those roots), MSA link-hours a week against the full subscription:

| position rule | `msh/US/#` | MO, IL and MeshSTL | MO and MeshSTL |
| --- | --- | --- | --- |
| keep 30 minutes (`lnk_02` as written), 500 m | 110 | 104 | 104 |
| keep 30 minutes, 3 km | 107 | 101 | 101 |
| stationarity window (proposed in ADR 0003), 500 m | 168 | 185 | 185 |
| stationarity window, 3 km | 162 | 178 | 178 |
| gateway-only | 852 | 852 | 852 |
| positions ignored | 1 194 | 1 194 | 1 194 |

With fewer positions, the nodes that count as stationary and the nodes that fall outside the 30-minute window both change: the 30-minute rule loses link-hours and the stationarity rule gains them.

## Options

1. Run mode. The three options were weighed on 2026-10-30; the first was chosen on 2026-10-31 and superseded on 2026-11-08 by the second, with the run parameters that give a window a built-in end.
   - Continuous with a byte cap, an optional end time and a halt marker. Advantage: data accrues unattended and cannot fill the disk. Cost: a halt stops ingestion until someone resumes it, and something is always running.
   - Windowed captures started by hand, as in the spike, with END_AT and MAX_RAW_BYTES as run parameters so a run always ends. Advantage: nothing runs unless asked, and each run is one immutable snapshot. Cost: gaps between windows; the spike showed that a window with no built-in end keeps going, which the parameters close.
   - Continuous with no cap under `unless-stopped`. Advantage: simplest. Cost: unbounded disk, and Docker restarts it whenever Docker Desktop starts.
2. What is stored.
   - Raw bytes of every delivery, decoded later (the plan). Advantage: a decoder bug is fixed by re-running decode. Cost: text messages and real ids on disk.
   - Raw bytes only for deliveries that fail to decode. Advantage: a decoder gap can be revisited. Cost: other channels' traffic is kept, with real ids in its clear header.
   - No raw bytes, allowlisted decoded fields with versions, fixtures for replay. Advantage: nothing private on disk. Cost: a decoder bug loses the affected fields for the hours it ran.
3. Where node ids are pseudonymised.
   - In dbt, in `dim_node` (the plan). Advantage: raw keeps real ids for debugging. Cost: real ids in every landed file and in `raw` and `staging`.
   - Unkeyed SHA-256 at capture. Advantage: no key to manage. Cost: node ids are 32-bit, so hashing all 4 294 967 296 of them reverses every hash in minutes on one laptop.
   - HMAC-SHA-256 with a secret key at capture. Advantage: no real id ever lands. Cost: losing or rotating the key breaks joins across the change.
4. Field selection.
   - Allowlist. Advantage: a new firmware field lands nowhere until someone adds it. Cost: each wanted field is a code change and a schema_version bump.
   - Denylist (everything except named fields). Advantage: new fields arrive free. Cost: a new private field lands by default.
5. Internet copies.
   - One row each. Advantage: full detail. Cost: about 28 million rows a day under `msh/US/#`.
   - Counts per (hour, topic, gateway). Advantage: about 135 000 rows a day under `msh/US/#`, and the duplication stays measurable. Cost: individual copies cannot be inspected.
   - Dropped. Advantage: nothing to store. Cost: no record of how much the broker duplicates.
6. Landing format.
   - JSON lines, as in the spike. Advantage: append-only, readable, survives a crash mid-hour. Cost: larger and needs a conversion step.
   - Parquet per flush, no compaction. Advantage: columnar and compressed at once. Cost: many small files an hour per table (12 at a 5-minute flush; the interval is set in the build).
   - Parquet per flush, merged to one file an hour. Advantage: one file an hour. Cost: a merge step rewrites files.
7. Subscription scope on the public broker.
   - `msh/US/#`. Advantage: the reference for every figure. Cost: 5.568 GB a day.
   - MO, IL and MeshSTL. Advantage: the same gateway-only and positions-ignored link-hours, and more under the stationarity rule (185 against 168 a week at 500 m). Cost: under the 30-minute rule 104 against 110 a week at 500 m and 101 against 107 at 3 km; IL adds 0.004 GB a day and no metro receipt.
   - MO and MeshSTL. Advantage: the same figures as MO, IL and MeshSTL in every row, at 0.013 GB a day. Cost: the same 30-minute loss, 104 against 110 and 101 against 107.
   - MeshSTL only. Advantage: the smallest feed. Cost: loses 1.2 and 0.9 percent of the gateway-only and positions-ignored link-hours; not simulated as a subscription.
8. Modem preset.
   - Not stored, since the plan fixes LongFast. Advantage: one column fewer. Cost: links on other presets would mix with LongFast unseen.
   - A column on every observation and a stratification term. Advantage: preset effects are measured, not assumed away. Cost: null for custom channel names.

## Decision

1. Capture on demand. A capture run takes a broker, a topic list, END_AT (ISO 8601 with an offset), MAX_RAW_BYTES (bytes, default 10 000 000 000) and an output snapshot id. It applies the allowlist of item 4 and the pseudonymisation of item 3 in memory, writes Parquet by hour into `snapshots/<id>/` on the host (`C:/Users/Casey/mesh-terrain-data/snapshots/<id>/`), outside every Docker volume, and at exit writes a README and a sha256 manifest of its files. It exits 0 when END_AT or MAX_RAW_BYTES (measured on the snapshot folder at each flush) is reached, recording the stop reason and the byte count in the manifest. There is no halt marker, no resume target, no restart policy other than `no`, no schedule and no sensor; nothing runs unless started. It is started by one documented make target or CLI command. Any run against the MeshSTL broker needs Casey's explicit approval first. Worked example of what MAX_RAW_BYTES means, JSON lines: at the `msh/US/#` rate, 5.568 GB a day, 10 GB trips in about 43 hours (as zstd Parquet with payloads, 0.220 GB a day, about 45 days). The public MO and MeshSTL roots alone, 0.013 GB a day, take about 769 days; with the MeshSTL broker added, 0.019 GB a day, about 526 days. Parquet of allowlisted fields is smaller still, so in practice END_AT ends a run and MAX_RAW_BYTES is the guard against a mistaken topic list.
2. No raw bytes are stored for any packet type. Each row carries decoder_version and schema_version. Replayability comes from committed fixtures per packet type, not from an archive.
3. Node and gateway ids pass through HMAC-SHA-256 with a key from the environment before landing. No real id, long name, short name or public key is ever written. Hardware model and role are kept. The dbt-side hash is removed, and contract rule `prv_01` is restated as "no real identifier exists in the landing zone or the warehouse" (see Decided 2026-10-31).
4. Only allowlisted fields land.
   - Envelope: received_at, gateway pseudonym, origin pseudonym, relay_node byte, hop_start, hop_limit, via_mqtt, rx_time, rx_snr, rx_rssi, channel, topic, modem preset parsed from the topic, portnum, payload length. Added 2026-10-31: `packet_id`, and beside each pseudonym its node's low id byte: `gateway_pseudonym` with `gateway_low_byte`, `origin_pseudonym` with `origin_low_byte`. Added 2026-11-08: `seen_public` and `seen_meshstl`, booleans on every stored row, true for each broker that delivered it (public `mqtt.meshtastic.org`, MeshSTL `mqtt.meshstl.org`); `delivery_key` is unchanged, so a packet both brokers carried is one row with both true. `pos_04` reads `seen_meshstl`.
   - Position: lat, lon, altitude, precision_bits, position timestamp, location_source, altitude_source.
   - MapReport (added 2026-11-08, Casey's option 1 with a source column): latitude, longitude, position_precision and altitude, from `e`-topic MapReport packets and from `map` topics, with the origin and gateway pseudonyms and their low bytes. Nothing else from a MapReport: no long name, short name, firmware version, region, preset or node count.
   - Every position row, whether from a Position packet or a MapReport, carries `position_source` with the value `position` or `mapreport`. MapReport precision is set separately on each node and is often coarser, so the stationarity rule and `pos_01` need to know where each position came from.
   - NodeInfo: hardware model, role.
   - NeighborInfo: neighbour pseudonym, snr, last heard. Added 2026-10-31: `neighbour_pseudonym` with `neighbour_low_byte`.
   - Telemetry: channel_utilization, air_util_tx, uptime, battery level, nothing else.
   - Every other packet type: portnum and payload length.
   A contract test asserts that no TEXT_MESSAGE_APP payload bytes are on disk.
5. Header-only internet copies are counted, not stored. A copy count row is keyed by (snapshot_id, hour, topic, gateway_pseudonym); a count of deliveries not stored as rows is keyed by (snapshot_id, hour, topic_class, reason), where reason is one of `json`, `stat` or `unparsed`. The word `malformed` is kept for the stored decode status of item 4's rows; `unparsed` names the counted class of `e`-topic records that do not parse as a `ServiceEnvelope` or have no sender. A rerun of the reduction over the same snapshot replaces that snapshot's count rows rather than adding to them, so running it twice gives the same table. Worked example: the full public capture would have stored about 28 million copy records a day; counted, it is 358 871 rows over 65 clock hours, median 3 978 an hour, about 135 000 a day. Under the decided scope there were none in the capture.
6. Landing is Parquet with zstd compression, one file per table per hour inside the snapshot folder. A run writes a flush file every flush interval under the hour it belongs to, and at exit merges each hour's flush files into one file, removing the flush files only after the merged file holds the same set of `delivery_key`, then writes the manifest. Flush files rather than an in-memory hour because a crash then loses at most one flush interval, not an hour; the merge at exit rather than a compaction asset because a run writes each hour once and a snapshot is never rewritten, so there is nothing for a later asset to do. A run that stops without writing a manifest has produced no snapshot: its folder is not read by the reduction, and the run is repeated under a new id. No JSON lines in production.
7. The topic list is a run parameter. The documented default for the public broker is `msh/US/MeshSTL/#` and `msh/US/MO/#`. `msh/US/IL/#` and `msh/US/#` are not in it: IL carries no metro receipt. The area is the MSA. Whether the MeshSTL broker itself (`mqtt.meshstl.org`, `msh/US/MeshSTL/#`) is captured again is ADR 0003's position-source question, pending Casey and the MeshSTL operators, and each such run needs Casey's explicit approval. A run connects to one broker; a snapshot records which.
8. Modem preset is a column on every observation and a stratification term in the validation model; it goes into the analytics-eng brief when Phase 4 starts.

### Decided 2026-10-31

Casey answered the four questions this ADR left open:

1. The envelope allowlist gains `packet_id` and the sender's low byte. Every pseudonymised id (gateway, origin, neighbour) has its node's low id byte stored beside it, in the columns named in item 4. `lnk_01` needs them: a radio receipt is keyed by (gateway, sender, packet id), and `relay_node` is matched against the `origin_low_byte` of the senders the gateway heard, the sender's own byte meaning a direct hop. A low byte is one of 256 values, the same kind of value as `relay_node`, and identifies no node on its own.
2. The topic lands with its gateway segment replaced by the gateway pseudonym: `msh/US/MeshSTL/2/e/LongFast/!a1b2c3d4` lands as `msh/US/MeshSTL/2/e/LongFast/` followed by the pseudonym, in observations and copy counts alike.
3. The copy of `NODE_ID_HMAC_KEY` is kept in Casey's password manager.
4. `prv_01` is scoped to the landing zone and the warehouse. The spike archive (gzipped JSON lines and Parquet on Casey's machine, evidence for ADR 0003) is outside it until it is deleted; deletion follows the pseudonymised reproduction gate in the next unit, under Casey's yes.

### Decided 2026-11-04

Casey answered the reviewer's two questions on PR #13:

1. MapReport: "Option 1, plus a position_source column with the values position or mapreport. MapReport precision is set separately on each node and is often coarser, so the stationarity rule and pos_01 need to know where each position came from." Recorded in item 4.
2. Broker: "Outside the key, but record every broker that delivered the row, not just the first: two boolean columns, seen_public and seen_meshstl, set to true for each broker that delivered it. delivery_key stays unchanged, so a packet both brokers carried is still one row. pos_04 uses seen_meshstl. The verification gate counts per broker using these columns and must reproduce the 560 vs 177 same-hours figures." Recorded in item 4; the figures are results.md's shared 24 hours under keep 30 minutes at 500 m, 560 link-hours a week on the MeshSTL broker against 177 on the public broker.

### Rescoped 2026-11-08

Casey: "There is no continuous or scheduled ingestion, now or planned." The project is a batch analysis on dated, immutable snapshots plus a reusable terrain-aware path-loss engine.

"Operating model: every input is a snapshot with an id, capture date, source URL, licence and sha256 manifest, recorded in the data contract. A refresh is a new snapshot id, never an overwrite. Re-running against the same snapshots must reproduce the same artifacts. Keep the Dagster asset graph for lineage. Remove schedules, sensors, retention sweeps and any automatic restart; nothing runs unless started."

"Observed data: the MQTT capture path stays in place and becomes an on-demand snapshot source, the same as the terrain sources. A capture run takes parameters (broker, topic list, END_AT, MAX_RAW_BYTES, output snapshot id), applies the ADR 0005 allowlist and pseudonymisation at capture time, writes Parquet, and exits. It is started by one documented make target or CLI command and never on a schedule. The existing data becomes snapshot meshtastic-capture-2026-10-22, the first output of that source, stored in C:/Users/Casey/mesh-terrain-data/snapshots/meshtastic-capture-2026-10-22/ outside every Docker volume, with a sha256 manifest and README. It is not treated as irreplaceable: no backup is required, and ADR 0003 records that if it is lost, its figures are reproducible only by a new capture under a new snapshot id. Not in git, not in LFS, not public. Precise MeshSTL positions must never be published. Any new capture from the MeshSTL broker needs my explicit approval first."

"The Missouri plus Illinois widening trigger is retired. The area is the MSA." Wherever the data is described: "a single capture from 2026-10-22 to 2026-10-25". Never imply ongoing collection.

The 2026-10-31 timing note (the capture service deferred, continuous ingestion later as a configuration change) is replaced by this subsection. Items 1, 5, 6 and 7 above carry the rescope; items 2, 3, 4 and 8 and the 2026-10-31 and 2026-11-04 answers stand as decided.

## Consequences

Contract rows, proposed wording for Casey's read; the lead edits `docs/data-quality-contract.md` after he approves:

| Id | Rule | Enforced by |
| --- | --- | --- |
| `ing_01` | Every stored delivery appears once, keyed by `delivery_key = HMAC-SHA-256(NODE_ID_HMAC_KEY, "delivery" \|\| len(topic) \|\| topic \|\| payload_bytes)`, with `len(topic)` the topic's byte length as a 4-byte big-endian integer, computed in memory before the bytes are dropped. No payload bytes are stored (ADR 0005). | unique test on `raw.mqtt_message.delivery_key`; the loader inserts with `ON CONFLICT (delivery_key) DO NOTHING`; known-answer test on the key derivation |
| `ing_02` | A snapshot's files are never updated or deleted after its manifest is written; a refresh is a new snapshot id. The reduction's count tables are replaced per snapshot id, never appended, so a rerun over the same snapshot leaves the same rows (ADR 0005). | Dagster asset check comparing each snapshot file's sha256 to its manifest before every load; no `UPDATE` or `DELETE` statement in any loader other than the per-snapshot replace of count tables; reviewer checks |
| `ing_03` | Every stored delivery carries a decode status in {`ok`, `unknown_port`, `decrypt_failed`, `stripped`, `malformed`}, set at decode on the same row. `stripped` means the broker forwarded the envelope header with no payload. Internet copies are counted once per (snapshot_id, hour, topic, gateway_pseudonym), and deliveries not stored as rows are counted per (snapshot_id, hour, topic_class, reason) with reason in {`json`, `stat`, `unparsed`}, so stored rows plus counted deliveries equal deliveries received (ADR 0005). | accepted_values on `raw.mqtt_message.decode_status` and on `reason`; unique tests on the two count keys; Dagster asset check reconciling received, stored and counted totals per snapshot and hour |
| `prv_01` | No real identifier exists in the landing zone or the warehouse. Node, gateway and neighbour ids appear only as HMAC pseudonyms, each with its node's low id byte (one of 256 values, not an identifier); topics carry the gateway pseudonym; no long name, short name or public key is stored (ADR 0005). | test that each landed Parquet table's schema equals the allowlist for its schema_version, the allowlist including `seen_public`, `seen_meshstl`, `position_source` and the MapReport columns latitude, longitude, position_precision and altitude; test that no stored string matches `![0-9a-f]{8}`; singular test scanning information_schema in every schema for `node_id`, `long_name`, `short_name` and public-key columns |
| `snp_01` (proposed, id and wording for the lead and Casey) | Every input is a snapshot with an id, a capture date, a source URL, a licence and a sha256 manifest of its files, recorded in this contract. A snapshot is never overwritten; a refresh is a new id. Re-running the pipeline against the same snapshot ids reproduces the same artifacts (ADR 0005, Casey's scope change of 2026-11-08). | a snapshot registry in the contract or a seed listing id, date, URL, licence and manifest path; Dagster asset check that every loaded file's sha256 matches its manifest; `make reproduce` run twice against the same snapshots gives identical artifact checksums |

The key is HMAC rather than SHA-256 because topic and envelope carry real ids: anyone holding a recording of the same feed could recompute an unkeyed hash and join back to them (option 3). The `delivery` prefix keeps it apart from the node pseudonyms under the same key, and the length prefix on the topic keeps two different (topic, payload) pairs from concatenating to the same bytes. In `ing_03`, `stripped` stays for header-only radio deliveries (25.8 percent of public radio deliveries, 84.1 percent of which carry `relay_node`), and the decoder sets `malformed` on a stored row whose portnum is 0 or below (wrong-key noise parsed with a negative portnum 3 times in 63.594 hours); `unparsed` is the counted class, never a stored status. The pipes in `ing_01` are escaped as `\|` so the table renders; the row on `main` leaves them bare.

Fit with `raw.mqtt_message`: no loader or `raw.mqtt_message` table exists in code yet, only in the plan and the agent file. `ing_01` and `ing_02` fit a `raw.mqtt_message` with one row per stored delivery, the allowlisted envelope columns, `snapshot_id` and `delivery_key` as its unique key; they do not fit the payload-keyed table the rules on `main` describe (columns `payload` and `key`), so the decoder unit creates the table with the new columns rather than loading into that design. `raw.decoded_packet` is no longer needed, because the decode status sits on the delivery row.

Other contract points (lead): `ing_04`'s decode-rate denominator must say whether `stripped` and `unparsed` or `malformed` deliveries count, and both `ing_04` and `ing_05` are written for a running service ("trailing 24 hours", "while the ingester is expected to be running"); under the rescope they apply per snapshot or go. Under `msh/US/MeshSTL` the public broker carried 18 378 parsed radio deliveries, 67.3 percent with a payload, and a further 28 342 records on `e` topics that do not parse as a `ServiceEnvelope` or have no sender, because some MeshSTL gateways publish other payloads there (results.md, "Regional segments"); the choice moves the rate a long way. The text-message test in item 4 becomes a new privacy rule, id from the lead, covering `json` payloads as well as `e` payloads. `pos_04` reads `seen_meshstl` for its source flag.

Superseded text in lead-only files (lead): `docs/PLAN.md`, "Store ciphertext and decoded payload both", the `raw` row of the data model, the Phase 1 exit and tasks (`raw.mqtt_message`, the loader keyed on the payload, decode as a later asset), and Phase 2 "`dim_node` snapshot with keyed-hash pseudonymisation"; `CLAUDE.md`, "Node ids are pseudonymised with a keyed hash before anything leaves the `core` schema"; `.claude/agents/ingest-eng.md`, the dumb-subscriber rule with decoding as a replayable batch step, raw columns with `payload` as binary, the payload-keyed loader, the decode asset, keeping ciphertext and decoded payload, and recording real envelopes as fixtures. By the scope change of 2026-11-08, also `docs/PLAN.md`'s streaming lane, the Phase 1 exit ("ran unattended 72 hours"), the Dagster sensor and the daily and nightly schedules, and ADR 0003's Missouri plus Illinois widening trigger; these go in the lead's Plan PR.

Capture and reduction (ingest-eng unless stated):

- `NODE_ID_HMAC_KEY` is read once as a secret, never logged, and listed in `.env.example` with no value. One key covers gateway, sender and neighbour ids, so a node has one pseudonym in every role (`lnk_03` compares them). Input encoding and output length are fixed by a known-answer test before the first landing, since changing either later equals a rotation. Losing or rotating the key breaks joins across the change, and with no raw bytes old rows cannot be recomputed; rotation is out of scope, and the backup copy is in Casey's password manager.
- `json` and `stat` topics are dropped and counted per (snapshot_id, hour, topic_class, reason) with no topic string: `json` carries decoded text and real ids in clear, and `stat` topics end in a node id. `map` topics are decoded for the MapReport allowlist of item 4 and nothing else; the names they carry are dropped in memory.
- A record on an `e` topic that does not parse as a `ServiceEnvelope` or has no sender (28 342 under `msh/US/MeshSTL` in the public capture) is neither an internet copy nor an allowlisted packet. It is counted with reason `unparsed` per (snapshot_id, hour, topic_class, reason), with no bytes and no topic string, and is not stored as a row.
- The 1 204 internet copies that kept a payload (116 default key, 1 077 plaintext, 11 PKI) are counted with the other copies per (snapshot_id, hour, topic, gateway_pseudonym); the payload is dropped undecoded.
- Snapshot `meshtastic-capture-2026-10-22` is the first output of this source: the reduction of the spike's public-broker capture (2026-10-22T16 to 2026-10-25T07) and MeshSTL-broker capture (its agreed 24 hours) into `C:/Users/Casey/mesh-terrain-data/snapshots/meshtastic-capture-2026-10-22/`, with its README and sha256 manifest. Both brokers are in the one snapshot, so the reduction sets `seen_public` and `seen_meshstl` in one pass, and the reproduction gate counts per broker with them (560 against 177). The snapshot is not irreplaceable: no backup is required, and if it is lost its figures are reproducible only by a new capture under a new snapshot id (ADR 0003 records this). It is not in git, not in LFS and never public. Precise MeshSTL positions are never published.
- `docs/adr/0003-spike/capture.py` is brought up to this design in place as the on-demand capture command in the decoder unit: not moved, not removed. It gains the allowlist, the pseudonymisation, Parquet output into `snapshots/<id>/`, the README and manifest at exit, and loses the halt marker and the refusal to start. Its README keeps the worked example of a run.
- Fixtures per packet type under `tests/fixtures/mqtt/` are the replay mechanism, a text message and a MapReport included. They are built, not recorded: made-up ids, invented text, re-encrypted with the default key, whose nonce uses packet id and sender id. None exist yet; the decoder unit builds them first. A decoder change bumps decoder_version, a change to landed columns bumps schema_version, and rows from a faulty decoder can only be found by decoder_version and excluded.
- The Parquet table split and names are proposed with warehouse-eng in the decoder unit brief.
- Modem preset: the channel segment of an `e` topic is the channel name, which equals the preset name only on default channels such as LongFast. The parser maps known preset names and leaves null for custom names; analytics-eng decides in Phase 4 whether null is its own level or excluded.

Other agents: platform-eng adds the make target that starts one capture run (broker, topic list, END_AT, MAX_RAW_BYTES, snapshot id), the bind mount of `C:/Users/Casey/mesh-terrain-data/snapshots/` and the `.env.example` entries `NODE_ID_HMAC_KEY`, `END_AT` and `MAX_RAW_BYTES`; no Compose service, since nothing runs unless started; warehouse-eng keys `dim_node` on the pseudonym and hashes nothing in dbt; analytics-eng adds modem preset as a stratification term at Phase 4.
