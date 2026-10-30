# ADR 0005: Ingest continuously under a byte cap, land only allowlisted and pseudonymised fields as hourly Parquet, and subscribe to the MeshSTL and MO roots

Date: 2026-10-30
Status: accepted
Proposed by: ingest-eng
Decided by: Casey

## Context

Phase 1 builds the ingester (`docs/PLAN.md`, Phase 1). The plan and the ingest-eng agent file describe a subscriber that lands every MQTT delivery with its payload bytes and a batch step that decodes later. The ADR 0003 spike captured the public broker that way for 63.594 observed hours and the MeshSTL broker for 24. It showed three things the plan did not expect: most of the volume is copies with nothing to decode, the feed carries people's text messages and real node ids that would land on disk as they are, and a capture with no stopping rule keeps running and restarts itself. Casey decided eight questions; this ADR records them.

Terms:

- delivery: one MQTT message from a broker, a `ServiceEnvelope` holding one radio packet and the id of the gateway that uploaded it. ADR 0003 calls it an envelope.
- internet copy: a delivery with the header flag `via_mqtt` set, meaning the gateway got the packet over the internet, not by radio. On the public broker every one is header-only: the broker removed the payload.
- radio receipt: a distinct (gateway, sender, packet id) among deliveries that parse, have a sender and have `via_mqtt` false. ADR 0003's link figures use only these.
- topic root: the segment after `msh/US` in a topic: `MeshSTL`, `MO`, `IL`, or `2` when the gateway sets none.
- HMAC: a keyed hash. HMAC-SHA-256(key, id) gives the same output for the same id and key; without the key nobody can compute or reverse it.
- pseudonym: the HMAC of a node id, stored in place of the id.
- modem preset: the radio setting a channel uses (bandwidth, spreading factor, coding rate), such as LongFast.
- halt marker: a small JSON file the ingester writes when it stops itself, holding the reason and the byte count.

Evidence, public broker, full capture, 63.594 observed hours, uncompressed JSON lines as the spike stored them with payloads in base64 (`docs/adr/0003-spike/results.md`, "Volume"):

| what | records | GB per day | share of bytes |
| --- | --- | --- | --- |
| everything under `msh/US/#` | 81 338 604 | 5.568 | 100.0% |
| internet copies (via_mqtt, header-only) | 74 314 521 | 4.936 | 88.6% |
| radio deliveries | 4 262 147 | 0.376 | 6.7% |
| regional roots MO, IL and MeshSTL only | 158 099 | 0.017 | |
| MeshSTL broker, its 24 hours | 23 946 | 0.006 | |

The full capture was 14.755 GB, 232 MB an hour. Under the regional roots there were only 9 internet copies in 63.594 hours, and the MeshSTL broker sends none.

Parquet compression of the spike's raw records: pending, filled by the lead.

Text messages: 142 003 decoded TEXT_MESSAGE_APP packets on the public broker over the capture (5.3 percent of decoded packets) and 38 on the MeshSTL broker in 24 hours. Decoding: wrong-key noise occasionally parses with a negative portnum (3 packets in 63.594 hours). Restarts: the spike containers ran past their windows under `--restart unless-stopped`, and Docker Desktop restarted them on 2026-10-28 (ADR 0003, Consequences).

Subscription scope, from results.md, "Subscription scope". Each row counts the radio receipts carrying `relay_node` at gateways positioned in the MSA that arrived under that root, and the link-hours kept when only those receipts are counted. Gateway-only link-hours have the relayer resolved and the gateway positioned; positions-ignored is ADR 0003's diagnostic with no position test.

| topic root | relayed metro receipts | share | gateway-only link-hours kept | share | positions-ignored link-hours kept | share |
| --- | --- | --- | --- | --- | --- | --- |
| `msh/US/MeshSTL` | 5 479 | 95.1% | 316 of 320 | 98.8% | 443 of 447 | 99.1% |
| `msh/US/MO` | 130 | 2.3% | 4 of 320 | 1.2% | 4 of 447 | 0.9% |
| `msh/US/IL` | 0 | 0.0% | 0 of 320 | 0.0% | 0 of 447 | 0.0% |
| `msh/US/2` (default root) | 151 | 2.6% | 0 of 320 | 0.0% | 0 of 447 | 0.0% |
| MeshSTL and MO together | 5 609 | 97.4% | 320 of 320 | 100.0% | 447 of 447 | 100.0% |

A simulated subscription to the regional roots only (`analyze.py --roots MO,IL,MeshSTL`, so positions and relayer candidates also come only from those roots) gives MSA weekly figures against the full subscription of 104 against 110 (keep 30 minutes, 500 m), 185 against 168 (stationarity window, 500 m) and 852 against 852 (gateway-only), and cuts volume from 5.568 GB a day to 0.017 GB a day.

## Options

1. Run mode.
   - Continuous with a byte cap, an optional end time and a halt marker. Advantage: data accrues unattended and cannot fill the disk. Cost: a halt stops ingestion until someone resumes it.
   - Windowed captures started by hand, as in the spike. Advantage: nothing runs unless asked. Cost: gaps between windows, and the spike showed that a window with no built-in end keeps going.
   - Continuous with no cap under `unless-stopped`. Advantage: simplest. Cost: unbounded disk, and Docker restarts it whenever Docker Desktop starts.
2. What is stored.
   - Raw bytes of every delivery, decoded later (the plan). Advantage: a decoder bug is fixed by re-running decode. Cost: text messages and real ids on disk.
   - Raw bytes only for deliveries that fail to decode. Advantage: a decoder gap can be revisited. Cost: other channels' traffic is kept, with real ids in its clear header.
   - No raw bytes, allowlisted decoded fields with versions, fixtures for replay. Advantage: nothing private on disk. Cost: a decoder bug loses the affected fields for the hours it ran.
3. Where node ids are pseudonymised.
   - In dbt, in `dim_node` (the plan). Advantage: raw keeps real ids for debugging. Cost: real ids in every landed file and in `raw` and `staging`.
   - Unkeyed SHA-256 at ingest. Advantage: no key to manage. Cost: node ids are 32-bit, so hashing all 4 294 967 296 of them reverses every hash in minutes on one laptop.
   - HMAC-SHA-256 with a secret key at ingest. Advantage: no real id ever lands. Cost: losing or rotating the key breaks joins across the change.
4. Field selection.
   - Allowlist. Advantage: a new firmware field lands nowhere until someone adds it. Cost: each wanted field is a code change and a schema_version bump.
   - Denylist (everything except named fields). Advantage: new fields arrive free. Cost: a new private field lands by default.
5. Internet copies.
   - One row each. Advantage: full detail. Cost: about 28 million rows a day under `msh/US/#`.
   - Counts per (hour, topic, gateway). Advantage: about 135 000 rows a day and the duplication stays measurable. Cost: individual copies cannot be inspected.
   - Dropped. Advantage: nothing to store. Cost: no record of how much the broker duplicates.
6. Landing format.
   - JSON lines, as in the spike. Advantage: append-only, readable, survives a crash mid-hour. Cost: larger and needs a conversion step.
   - Parquet per flush, no compaction. Advantage: columnar and compressed at once. Cost: 12 small files an hour per table at a 5-minute flush.
   - Parquet per flush with hourly compaction. Advantage: one file an hour once compacted. Cost: compaction rewrites files.
7. Subscription scope on the public broker.
   - `msh/US/#`. Advantage: everything. Cost: 5.568 GB a day for no extra metro link-hour.
   - MO, IL and MeshSTL. Advantage: every metro link-hour. Cost: IL adds volume and no metro receipt.
   - MeshSTL and MO. Advantage: every metro link-hour (320 of 320 and 447 of 447). Cost: drops the default root's 2.6 percent of relayed metro receipts, which carry no link-hour.
   - MeshSTL only. Advantage: the smallest feed. Cost: loses 1.2 and 0.9 percent of the link-hours.
8. Modem preset.
   - Not stored, since the plan fixes LongFast. Advantage: one column fewer. Cost: LongTurbo and LongFast links would mix unseen.
   - A column on every observation and a stratification term. Advantage: preset effects are measured, not assumed away. Cost: null for custom channel names.

## Decision

1. Continuous ingestion from Phase 1. The ingester runs until MAX_RAW_BYTES (default 10 GB, measured on the landing folder at each flush) or an optional END_AT, then flushes, writes a halt marker with the reason and byte count, logs at error and exits 0. Its restart policy is on-failure, so a crash restarts it and a halt does not. A Dagster asset check surfaces the halt marker, and a make target resumes. Worked example: at the `msh/US/#` rate in JSON lines, 5.568 GB a day, 10 GB trips in about 43 hours. At the decided scope in the same JSON lines terms, 0.017 GB a day from the public regional roots (which still include IL) plus 0.006 from the MeshSTL broker, it takes about 435 days, well over a year; Parquet of allowlisted fields is smaller still.
2. No raw bytes are stored for any packet type. Each row carries decoder_version and schema_version. Replayability comes from committed fixtures per packet type, not from an archive. The decoder rejects a portnum of 0 or below.
3. Node and gateway ids pass through HMAC-SHA-256 with a key from the environment before landing. No real id, long name, short name or public key is ever written. Hardware model and role are kept. The dbt-side hash is removed, and contract rule `prv_01` is restated as "no real identifier exists on disk".
4. Only allowlisted fields land.
   - Envelope: received_at, gateway pseudonym, origin pseudonym, relay_node byte, hop_start, hop_limit, via_mqtt, rx_time, rx_snr, rx_rssi, channel, topic, modem preset parsed from the topic, portnum, payload length.
   - Position: lat, lon, altitude, precision_bits, position timestamp, location_source, altitude_source.
   - NodeInfo: hardware model, role.
   - NeighborInfo: neighbour pseudonym, snr, last heard.
   - Telemetry: channel_utilization, air_util_tx, uptime, battery level, nothing else.
   - Every other packet type: portnum and payload length.
   A contract test asserts that no TEXT_MESSAGE_APP payload bytes are on disk.
5. Header-only internet copies are counted per (hour, topic, gateway), not stored. Worked example: the full public capture would have stored about 28 million copy records a day; counted, it is 358 871 rows over 65 clock hours, median 3 978 an hour, about 135 000 a day. Under the decided scope there were 9 copies in 63.594 hours, so this matters mostly if the scope ever widens.
6. Landing is Parquet with zstd compression, written from the flush buffer under hourly partitions, with an hourly compaction asset. No JSON lines in production.
7. On the public broker the ingester subscribes to `msh/US/MeshSTL/#` and `msh/US/MO/#`. `msh/US/IL/#` and `msh/US/#` are not subscribed: IL carries no metro receipt, and widening to Missouri plus Illinois is not proposed in ADR 0003. Whether the MeshSTL broker itself (`mqtt.meshstl.org`, `msh/US/MeshSTL/#`) is also subscribed is ADR 0003's position-source question, pending Casey and the MeshSTL operators. The ingester supports more than one broker connection, and the scope list is configuration.
8. Modem preset is a column on every observation and a stratification term in the validation model; it goes into the analytics-eng brief when Phase 4 starts.

## Consequences

Contract rules (lead; restated wording needs Casey's approval). Proposals only for the first three:

- `ing_01` keys each delivery on `sha256(topic || payload_bytes)`, and no payload bytes are stored now. Proposed: "Every stored delivery appears once, keyed by `delivery_key = sha256(topic || payload_bytes)` computed in memory before the bytes are dropped; the bytes are never stored." Enforced by a unique test on `delivery_key`.
- `ing_02` forbids updating raw rows, and compaction rewrites files. Proposed: "Landed rows are never updated or deleted. The only rewrite is the hourly compaction, which replaces a finished hour's flush files with one file and removes them only after the new file holds the same set of `delivery_key`." Enforced by an asset check on the key sets.
- `ing_03` pairs every raw row with a later decode row; decoding now happens at ingest, and internet copies are counted, not stored. Proposed: "Every stored delivery carries a decode status in {`ok`, `unknown_port`, `decrypt_failed`, `stripped`, `malformed`}, set at ingest on the same row. Internet copies are not stored; each is counted once per (hour, topic, gateway), so stored rows plus counted copies equal deliveries received." `stripped` stays for radio deliveries that arrive header-only (25.8 percent of public radio deliveries, which still carry `relay_node`).
- `prv_01` is restated by Casey as "no real identifier exists on disk"; the lead applies it in this PR.
- The text-message test in item 4 becomes a new privacy rule; the lead assigns its id.

Lead-only text that no longer matches (lead):

- `docs/PLAN.md`: "Store ciphertext and decoded payload both" (decryption paragraph); the `raw` row of the data model and the Phase 1 exit and tasks (`raw.mqtt_message`, the loader keyed on the payload, decode as a later asset with decrypt); Phase 2 "`dim_node` snapshot with keyed-hash pseudonymisation", now at ingest.
- `CLAUDE.md`: "Node ids are pseudonymised with a keyed hash before anything leaves the `core` schema".
- `.claude/agents/ingest-eng.md`, superseded design rules: "The subscriber is dumb on purpose" with decoding as a replayable batch step; raw Parquet columns with `payload` as binary; the loader keyed on the payload; the decode asset; "Keep both the ciphertext and the decoded payload"; and "Capture one real envelope per portnum once, commit it", which the fixtures bullet below replaces.

Ingester (ingest-eng unless stated):

- The HMAC key is a new variable, `NODE_ID_HMAC_KEY`, read once through the settings object as a secret, never logged, and listed in `.env.example` with no value. One key and one function cover gateway, sender and neighbour ids, so a node has one pseudonym in every role; `lnk_03` compares them. The input encoding and output length are left open and are fixed with a known-answer test before the first landing, because changing either later is the same as rotating the key. Losing or rotating the key breaks joins across the change, and with no raw bytes old rows cannot be recomputed. Rotation is out of scope for Phase 1; the key needs a copy outside the repo and the machine (Casey).
- A topic such as `msh/US/MeshSTL/2/e/LongFast/!a1b2c3d4` ends in the real gateway id. Read together, items 3 and 4 mean the topic lands with that segment replaced by the gateway pseudonym, in observations and copy counts alike. Casey to confirm.
- The allowlist omits the packet id and the sender's low byte. ADR 0003's link rule matches `relay_node` against the low byte of each sender's real id, which the pseudonym hides, and a radio receipt is keyed by (gateway, sender, packet id). Without both fields the relayer cannot be resolved. Open for Casey: add `packet_id` and the origin's low byte (one of 256 values, the same kind of value as `relay_node`) to the envelope allowlist, or name another way.
- Fixtures per packet type under `tests/fixtures/mqtt/` are the replay mechanism, a text message included so the contract test has something to catch. Under the restated `prv_01` they are built, not recorded as they are: made-up node and gateway ids, invented text, re-encrypted with the default key (the nonce uses packet id and sender id, so a changed sender means re-encrypting). A decoder change bumps decoder_version; a change to the landed columns bumps schema_version. Rows from a faulty decoder cannot be corrected, only found by decoder_version and excluded.
- How the allowlisted fields split into Parquet tables (one wide table or one per packet type) and their names are open; ingest-eng proposes them with warehouse-eng in the Phase 1 brief. On the object store the landing folder for MAX_RAW_BYTES is the bucket prefix.
- Modem preset: in `msh/US/MeshSTL/2/e/LongFast/!gatewayid` the channel segment is the channel name, which equals the preset name only on default channels (seen: LongFast, LongTurbo; custom names seen: NerdStuff, secure). The parser maps known preset names and leaves the preset null for custom names. The validation model treats null as its own level or excludes it; analytics-eng decides in Phase 4.
- The scope simulation included IL. IL carries no metro receipt, but its positions or relayer candidates could still feed a metro link; rerun `analyze.py --roots MO,MeshSTL` before the first week of ingestion to confirm the figures hold.
- Docker documents that on-failure does not restart a container when the daemon restarts. A container running when Docker Desktop quits records a non-zero exit (255 for the warehouse and object store on 2026-10-28). Verify on Casey's host that the ingester is not restarted then (PR #12, Status). If it is not, ingestion stops until resumed and `ing_05` freshness is the alarm (platform-eng and ingest-eng).

Other agents:

- platform-eng: the ingester Compose service with `restart: on-failure`, the one exception to PR #12's `restart: "no"`, with the compose-smoke guard excluding it; the resume make target; `.env.example` entries for `MAX_RAW_BYTES`, `END_AT` and `NODE_ID_HMAC_KEY`.
- warehouse-eng: `dim_node` keys on the pseudonym; dbt hashes nothing.
- analytics-eng, at Phase 4: modem preset as a stratification term (item 8).
- The spike's raw archive (gzipped JSON lines and Parquet on Casey's machine) is outside this rule. It is evidence for ADR 0003 and stays off the repo (Casey).
