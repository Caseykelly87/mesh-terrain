# ADR 0005: Ingest continuously under a byte cap, land only allowlisted and pseudonymised fields as hourly Parquet, and subscribe to the MeshSTL and MO roots

Date: 2026-10-30
Status: accepted
Proposed by: ingest-eng
Decided by: Casey

## Context

Phase 1 builds the ingester (`docs/PLAN.md`, Phase 1). The plan and the ingest-eng agent file describe a subscriber that lands every MQTT delivery with its payload bytes and a batch step that decodes later. The ADR 0003 spike captured the public broker that way for 63.594 observed hours and the MeshSTL broker for 24. It showed three things the plan did not expect: most of the volume is copies with nothing to decode, the feed carries people's text messages and real node ids that would land on disk as they are, and a capture with no stopping rule keeps running and restarts itself. Casey decided eight questions; this ADR records them. It depends on PR #9 (ADR 0003 and its results) and PR #12 (Compose restart policy) and merges after them.

Terms:

- delivery: one MQTT message from a broker. On an `e` topic (`msh/US/MeshSTL/2/e/LongFast/!gatewayid`) it is a `ServiceEnvelope` holding one radio packet and the id of the gateway that uploaded it; ADR 0003 calls it an envelope. Brokers also publish `json`, `map` and `stat` topics.
- internet copy: a delivery with the header flag `via_mqtt` set, meaning the gateway got the packet over the internet, not by radio. On the public broker all but 1 204 of 74 314 521 are header-only: the broker removed the payload.
- radio receipt: a distinct (gateway, sender, packet id) among deliveries that parse, have a sender and have `via_mqtt` false, the gateway's own packets excluded. ADR 0003's link figures use only these.
- topic root: the segment after `msh/US` in a topic: `MeshSTL`, `MO`, `IL`, or `2` when the gateway sets none.
- HMAC: a keyed hash. HMAC-SHA-256(key, id) gives the same output for the same id and key; without the key nobody can compute or reverse it.
- pseudonym: the HMAC of a node id, stored in place of the id.
- modem preset: the radio setting a channel uses (bandwidth, spreading factor, coding rate), such as LongFast.
- halt marker: a small JSON file the ingester writes when it stops itself, holding the reason and the byte count.

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
   - Counts per (hour, topic, gateway). Advantage: about 135 000 rows a day under `msh/US/#`, and the duplication stays measurable. Cost: individual copies cannot be inspected.
   - Dropped. Advantage: nothing to store. Cost: no record of how much the broker duplicates.
6. Landing format.
   - JSON lines, as in the spike. Advantage: append-only, readable, survives a crash mid-hour. Cost: larger and needs a conversion step.
   - Parquet per flush, no compaction. Advantage: columnar and compressed at once. Cost: many small files an hour per table (12 at a 5-minute flush; the interval is set in the build).
   - Parquet per flush with hourly compaction. Advantage: one file an hour once compacted. Cost: compaction rewrites files.
7. Subscription scope on the public broker.
   - `msh/US/#`. Advantage: the reference for every figure. Cost: 5.568 GB a day.
   - MO, IL and MeshSTL. Advantage: the same gateway-only and positions-ignored link-hours, and more under the stationarity rule (185 against 168 a week at 500 m). Cost: under the 30-minute rule 104 against 110 a week at 500 m and 101 against 107 at 3 km; IL adds 0.004 GB a day and no metro receipt.
   - MO and MeshSTL. Advantage: the same figures as MO, IL and MeshSTL in every row, at 0.013 GB a day. Cost: the same 30-minute loss, 104 against 110 and 101 against 107.
   - MeshSTL only. Advantage: the smallest feed. Cost: loses 1.2 and 0.9 percent of the gateway-only and positions-ignored link-hours; not simulated as a subscription.
8. Modem preset.
   - Not stored, since the plan fixes LongFast. Advantage: one column fewer. Cost: links on other presets would mix with LongFast unseen.
   - A column on every observation and a stratification term. Advantage: preset effects are measured, not assumed away. Cost: null for custom channel names.

## Decision

1. Continuous ingestion from Phase 1. The ingester runs until MAX_RAW_BYTES (default 10 GB, 10 000 000 000 bytes, measured on the landing folder at each flush) or an optional END_AT, then flushes, writes a halt marker with the reason and byte count, logs at error and exits 0. Its restart policy is on-failure, so a crash restarts it and a halt does not. A Dagster asset check surfaces the halt marker, and a make target resumes. Worked example, JSON lines: at the `msh/US/#` rate, 5.568 GB a day, 10 GB trips in about 43 hours (as zstd Parquet with payloads, 0.220 GB a day, about 45 days). The public MO and MeshSTL roots alone, 0.013 GB a day, take about 769 days; with the pending MeshSTL broker subscription added, 0.019 GB a day, about 526 days. Parquet of allowlisted fields is smaller still.
2. No raw bytes are stored for any packet type. Each row carries decoder_version and schema_version. Replayability comes from committed fixtures per packet type, not from an archive.
3. Node and gateway ids pass through HMAC-SHA-256 with a key from the environment before landing. No real id, long name, short name or public key is ever written. Hardware model and role are kept. The dbt-side hash is removed, and contract rule `prv_01` is restated as "no real identifier exists on disk".
4. Only allowlisted fields land.
   - Envelope: received_at, gateway pseudonym, origin pseudonym, relay_node byte, hop_start, hop_limit, via_mqtt, rx_time, rx_snr, rx_rssi, channel, topic, modem preset parsed from the topic, portnum, payload length.
   - Position: lat, lon, altitude, precision_bits, position timestamp, location_source, altitude_source.
   - NodeInfo: hardware model, role.
   - NeighborInfo: neighbour pseudonym, snr, last heard.
   - Telemetry: channel_utilization, air_util_tx, uptime, battery level, nothing else.
   - Every other packet type: portnum and payload length.
   A contract test asserts that no TEXT_MESSAGE_APP payload bytes are on disk.
5. Header-only internet copies are counted per (hour, topic, gateway), not stored. Worked example: the full public capture would have stored about 28 million copy records a day; counted, it is 358 871 rows over 65 clock hours, median 3 978 an hour, about 135 000 a day. Under the decided scope there were none in the capture, so this matters only if the scope widens.
6. Landing is Parquet with zstd compression, written from the flush buffer under hourly partitions, with an hourly compaction asset. No JSON lines in production.
7. On the public broker the ingester subscribes to `msh/US/MeshSTL/#` and `msh/US/MO/#`. `msh/US/IL/#` and `msh/US/#` are not subscribed: IL carries no metro receipt, and widening to Missouri plus Illinois is not proposed in ADR 0003. Whether the MeshSTL broker itself (`mqtt.meshstl.org`, `msh/US/MeshSTL/#`) is also subscribed is ADR 0003's position-source question, pending Casey and the MeshSTL operators. The ingester supports more than one broker connection, and the scope list is configuration.
8. Modem preset is a column on every observation and a stratification term in the validation model; it goes into the analytics-eng brief when Phase 4 starts.

## Consequences

Contract wording, proposed by ingest-eng; Casey approves, then the lead edits `docs/data-quality-contract.md`:

- `ing_01`: "Every stored delivery appears once, keyed by `delivery_key = HMAC-SHA-256(NODE_ID_HMAC_KEY, "delivery" || topic || payload_bytes)`, computed in memory before the bytes are dropped." It is keyed because topic and envelope carry real ids: anyone holding a recording of the same feed, the spike archive included, could recompute an unkeyed SHA-256 and join back to them (option 3). It uses the node-id key with a fixed `delivery` prefix rather than a second key: one secret to hold, and the prefix keeps the two uses apart. Enforced by a unique test on `delivery_key`.
- `ing_02`: "Landed rows are never updated or deleted. The only rewrite is the hourly compaction, which replaces a finished hour's flush files with one file and removes them only after the new file holds the same set of `delivery_key`." Enforced by an asset check on the key sets.
- `ing_03`: "Every stored delivery carries a decode status in {`ok`, `unknown_port`, `decrypt_failed`, `stripped`, `malformed`}, set at ingest on the same row. Internet copies are counted once per (hour, topic, gateway), and deliveries that are not stored as rows are counted per (hour, topic class), so stored rows plus counted deliveries equal deliveries received." `stripped` stays for header-only radio deliveries (25.8 percent of public radio deliveries, 84.1 percent of which carry `relay_node`). The decoder sets `malformed` on a portnum of 0 or below: wrong-key noise parsed with a negative portnum 3 times in 63.594 hours.
- `prv_01` enforcement, replacing the column-name scan: the landed Parquet schema of each table equals the allowlist for its schema_version; no stored string matches `![0-9a-f]{8}` (a node id as it appears in topics); no column holds a long name, short name or public key.
- `ing_04`: its decode-rate denominator must say whether `stripped` and unparsed or `malformed` deliveries count. Under `msh/US/MeshSTL` the public broker carried 18 378 parsed radio deliveries, 67.3 percent with a payload, and a further 28 342 records on `e` topics that do not parse as a `ServiceEnvelope` or have no sender, because some MeshSTL gateways publish other payloads there (results.md, "Regional segments"). With 28 342 unparsed against 18 378 parsed, the choice moves the rate a long way.
- The text-message test in item 4 becomes a new privacy rule, id from the lead, and covers `json` payloads as well as `e` payloads.

Superseded text in lead-only files (lead): `docs/PLAN.md`, "Store ciphertext and decoded payload both", the `raw` row of the data model, the Phase 1 exit and tasks (`raw.mqtt_message`, the loader keyed on the payload, decode as a later asset), and Phase 2 "`dim_node` snapshot with keyed-hash pseudonymisation"; `CLAUDE.md`, "Node ids are pseudonymised with a keyed hash before anything leaves the `core` schema"; `.claude/agents/ingest-eng.md`, the dumb-subscriber rule with decoding as a replayable batch step, raw columns with `payload` as binary, the payload-keyed loader, the decode asset, keeping ciphertext and decoded payload, and recording real envelopes as fixtures.

Ingester (ingest-eng unless stated):

- Halt and resume. While the halt marker exists the ingester refuses to start: it logs at error and exits 0, as `capture.py` does. The resume target removes the marker and starts the service; it never deletes landed data. It refuses while the halt condition still holds: after a `max_raw_bytes` halt, until the landing folder is below MAX_RAW_BYTES (Casey raised the cap or moved files); after an `end_at` halt, until END_AT is unset or in the future. It prints the size and the cap either way. On the object store the landing folder is the bucket prefix.
- `NODE_ID_HMAC_KEY` is read once as a secret, never logged, and listed in `.env.example` with no value. One key covers gateway, sender and neighbour ids, so a node has one pseudonym in every role (`lnk_03` compares them). Input encoding and output length are fixed by a known-answer test before the first landing, since changing either later equals a rotation. Losing or rotating the key breaks joins across the change, and with no raw bytes old rows cannot be recomputed; rotation is out of scope for Phase 1.
- `json`, `map` and `stat` topics are dropped and counted per (hour, topic class) with no topic string: `json` carries decoded text and real ids in clear, map reports carry names, and `stat` topics end in a node id.
- A record on an `e` topic that does not parse as a `ServiceEnvelope` or has no sender (28 342 under `msh/US/MeshSTL` in the public capture) is neither an internet copy nor an allowlisted packet. It gets status `malformed` and is counted per (hour, topic class) the same way, with no bytes and no topic string.
- The 1 204 internet copies that kept a payload (116 default key, 1 077 plaintext, 11 PKI) are counted with the other copies per (hour, topic, gateway); the payload is dropped undecoded.
- Fixtures per packet type under `tests/fixtures/mqtt/` are the replay mechanism, a text message included. They are built, not recorded: made-up ids, invented text, re-encrypted with the default key, whose nonce uses packet id and sender id. A decoder change bumps decoder_version, a change to landed columns bumps schema_version, and rows from a faulty decoder can only be found by decoder_version and excluded.
- The Parquet table split and names are proposed with warehouse-eng in the Phase 1 brief.
- Modem preset: the channel segment of an `e` topic is the channel name, which equals the preset name only on default channels such as LongFast. The parser maps known preset names and leaves null for custom names; analytics-eng decides in Phase 4 whether null is its own level or excluded.
- Docker documents that on-failure does not restart a container when the daemon restarts, yet a container running when Docker Desktop quits records a non-zero exit (255 for the warehouse and object store on 2026-10-28). Verify on Casey's host that the ingester is not restarted then (PR #12). If not, `ing_05` freshness is the alarm (platform-eng and ingest-eng).

Other agents: platform-eng adds the ingester Compose service with `restart: on-failure` (the one exception to PR #12's `restart: "no"`, excluded from the compose-smoke guard), the resume target and the `.env.example` entries `MAX_RAW_BYTES`, `END_AT` and `NODE_ID_HMAC_KEY`; warehouse-eng keys `dim_node` on the pseudonym and hashes nothing in dbt; analytics-eng adds modem preset as a stratification term at Phase 4.

Open for Casey:

1. The allowlist omits the packet id and the sender's low byte. ADR 0003's link rule matches `relay_node` against the low byte of each sender's real id, which the pseudonym hides, and a radio receipt is keyed by (gateway, sender, packet id); without both the relayer cannot be resolved. Add `packet_id` and the origin's low byte (one of 256 values, like `relay_node`), or name another way.
2. An `e` topic ends in the real gateway id. Read together, items 3 and 4 mean the topic lands with that segment replaced by the gateway pseudonym, in observations and copy counts alike. Confirm.
3. Where a copy of `NODE_ID_HMAC_KEY` is kept outside the repo and the machine.
4. The spike's raw archive (gzipped JSON lines and Parquet on Casey's machine, evidence for ADR 0003) holds real ids, so it conflicts with the unscoped `prv_01`. Either scope the rule to the landing zone and the warehouse, or set a retention date for the archive.
