# Data-quality contract

Every rule here has a dbt test or a Dagster asset check with the same identifier. A unit test in `tests/test_contract.py` parses this file and fails if a rule has no matching test. Change a rule only with an ADR and Casey's approval.

Identifiers are stable. Tests are named `assert_<identifier>` (dbt singular) or declared in the model YAML with `meta: {contract: <identifier>}`.

## Ingestion

| Id | Rule | Enforced by |
| --- | --- | --- |
| `ing_01` | Every stored delivery appears once per snapshot, keyed by (`snapshot_id`, `delivery_key`) with `delivery_key = HMAC-SHA-256(NODE_ID_HMAC_KEY, "delivery" \|\| len(topic) \|\| topic \|\| payload_bytes)`, `len(topic)` being the topic's byte length as a 4-byte big-endian integer, computed in memory before the bytes are dropped. A delivery present in two snapshots is one row in each. The broker flags `seen_public` and `seen_meshstl` are set during reduction, before load, so a delivery both brokers carried is one row with both true and the loader's insert-ignore never drops a sighting. No payload bytes are stored (ADR 0005). | unique test on (`snapshot_id`, `delivery_key`) in `raw.mqtt_message`; the loader inserts with `ON CONFLICT (snapshot_id, delivery_key) DO NOTHING`; known-answer test on the key derivation, including a delivery seen on both brokers that lands as one row with both flags true |
| `ing_02` | Snapshot files are never modified or deleted after the manifest is written, except by Casey from a listed set of files he has approved (`snp_01`). | sha256 check of every file against the manifest before each load; no code path writes into a finished snapshot folder; reviewer checks |
| `ing_03` | Every stored delivery carries a decode status in {`ok`, `unknown_port`, `decrypt_failed`, `stripped`, `malformed`}, set at decode on the same row. `stripped` means the broker forwarded the envelope header with no payload. Internet copies are counted once per (snapshot_id, hour, topic, gateway_pseudonym), and deliveries not stored as rows are counted per (snapshot_id, hour, topic_class, reason) with reason in {`json`, `stat`, `unparsed`, `other`}: `unparsed` is a record on an `e` or `map` topic that does not parse as the expected message or has no sender, `other` a topic outside `e`, `json`, `map` and `stat`. Stored rows plus counted deliveries equal deliveries received (ADR 0005). | accepted_values on `raw.mqtt_message.decode_status` and on `reason`; unique tests on the two count keys; Dagster asset check reconciling received, stored and counted totals per snapshot and hour |
| `ing_04` | Decode success rate over the trailing 24 hours is at least 90 percent. | Dagster asset check `decode_rate` |
| `ing_05` | The newest raw file is less than 15 minutes old while the ingester is expected to be running. | Dagster asset check `raw_freshness` |
| `ing_06` | Ordering uses `received_at` (our clock). `rx_time` is stored but never used as a key. | reviewer checks; `rx_time` absent from any `ORDER BY` or window |
| `ing_07` | Warehouse rows in `raw` are never updated or deleted one by one. The loader inserts with `ON CONFLICT DO NOTHING` on (`snapshot_id`, `delivery_key`), (`snapshot_id`, `hour`, `topic`, `gateway_pseudonym`) and (`snapshot_id`, `hour`, `topic_class`, `reason`), so a second load changes nothing. When the decoder changes, the warehouse is rebuilt from the snapshots and `decoder_version` on every row records which build produced it (ADR 0005). | no `UPDATE` or `DELETE` statement in any loader; unique tests on the three keys; Dagster asset check that a second load of a snapshot changes no row count; not_null on `decoder_version` |

## Snapshots

| Id | Rule | Enforced by |
| --- | --- | --- |
| `snp_01` | Every input is a snapshot with an id, a capture date, a source URL, a licence and a sha256 manifest of its files, recorded in `raw.snapshot`. A snapshot is never overwritten: no file in it changes after the manifest is written, and a refresh is a new id. A run writes into `<id>.partial/` and renames it to `<id>/` only after the manifest is written; loaders and the reduction ignore `.partial` folders, nothing deletes them automatically, and each run report lists them for Casey. Every file read from a snapshot matches its sha256 in the manifest. Re-running the pipeline against the same snapshot ids reproduces every artifact: the same data content hash (the hash of the rows or cells, not of the file, since Parquet and COG writers embed timestamps). Every random draw, endpoint sampling included, uses a seed recorded in the manifest (ADR 0005; Casey's scope change of 2026-11-08). | `raw.snapshot` row per snapshot with id, date, URL, licence and manifest path; Dagster asset check comparing each file's sha256 to the manifest before every load; `make reproduce` run twice against the same snapshots gives identical content hashes; test that every manifest records its seeds |

## Positions

| Id | Rule | Enforced by |
| --- | --- | --- |
| `pos_01` | `position_quality` is `usable` when `precision_bits >= 15`, `coarse` for 12 to 14, else `unusable`; `box_half_width_m` is derived from `precision_bits` and latitude and stored on the row. | accepted_values + singular test on the mapping and the derivation |
| `pos_04` | A position with `precision_bits >= 20` is flagged `precise_source = true`; its source (regional broker or published-key channel) is recorded in `dim_node` or the position row. Public-broker default-key positions never carry this flag. | singular test |
| `pos_02` | Only positions inside the MSA polygon enter `core`. | singular test `ST_Within` against the seeded boundary |
| `pos_03` | Latitude in [-90, 90], longitude in [-180, 180], not both zero. | range tests + singular test |

## Direct links

| Id | Rule | Enforced by |
| --- | --- | --- |
| `lnk_01` | A receipt enters `fct_rx_observation` only when it is a radio receipt (`via_mqtt` false, the gateway's own packets excluded) carrying a non-zero `relay_node`, and its last relayer is resolved to exactly one known node (ADR 0003). Candidates are the senders of every radio delivery the same gateway heard in the 24 hours before the receipt's `received_at`, its own packets included. `relay_node` equal to the sender's own low byte is a direct hop; otherwise exactly one candidate has that low byte. A NeighborInfo pair resolves it when the gateway's own NeighborInfo reports in the same 24 hours list exactly one neighbour with that low byte. When the two resolve to different nodes the receipt is dropped; when they agree, or only one resolves, `link_source` names `relay_node` if it resolved and `neighbor_info` otherwise. `hop_limit` is never used to decide this. | singular test on `core.fct_rx_observation`; accepted_values on `link_source` (`relay_node`, `neighbor_info`); reviewer checks no model references `hop_limit` for link logic |
| `lnk_02` | Both relayer and gateway have a `usable` position for the receipt (ADR 0003). A node is stationary when it sent at least one position in the 7 days before the receipt's `received_at` and all of them, from any area and either source (Position or MapReport, read from staging, not core), share one `usable` box (same quantised latitude, longitude and `precision_bits`); it keeps that box. Any other node needs a `usable` position within 30 minutes of the receipt. | singular test |
| `lnk_03` | Relayer and gateway are different nodes. | singular test |
| `lnk_04` | A link-hour has at least 3 packets. | singular test on `core.int_link_window` |
| `lnk_05` | Link distance is between 500 m and 60 000 m, and `position_uncertainty_m` (sum of both box half-widths) is stored. Validation marts additionally require `distance_m >= 3000` unless both endpoints are `precise_source`. | range test on `distance_m`; singular test on the mart filter |
| `lnk_06` | RSSI in [-140, 0] dBm; SNR in [-25, 15] dB. | range tests |
| `lnk_07` | Every link geometry is valid and is a two-point LINESTRING. | singular test `ST_IsValid` and `ST_NumPoints = 2` |
| `lnk_08` | Grain of `int_link_window` is (tx_node_hash, gateway_node_hash, hour). | unique test on the composite key |

## Privacy

| Id | Rule | Enforced by |
| --- | --- | --- |
| `prv_01` | No real identifier exists in the landing zone or the warehouse. Node, gateway and neighbour ids appear only as HMAC pseudonyms, each with its node's low id byte (one of 256 values, not an identifier); topics carry the gateway pseudonym; no long name, short name or public key is stored. The one exception is the raw capture archive of 2026-10-22, which holds real ids until the reproduction gate passes and Casey approves its deletion; the rule applies without exception to everything the reduction writes (ADR 0005). | test that each landed Parquet table's schema equals the allowlist for its schema_version, the allowlist including `seen_public`, `seen_meshstl`, `position_source` and the MapReport columns latitude, longitude, position_precision and altitude; test that no stored string matches `![0-9a-f]{8}`; singular test scanning information_schema in every schema for `node_id`, `long_name`, `short_name` and public-key columns |
| `prv_02` | Public exports contain pseudonymised ids only and no node names. | export asset test |
| `prv_03` | Hex cells with fewer than 5 link-hours are null in public outputs. | singular test on `analytics.mart_hex` |

## Terrain features

| Id | Rule | Enforced by |
| --- | --- | --- |
| `ter_01` | Every link-hour in `core.int_link_window` has a `features.link_terrain` row with `terrain_status` in {`ok`, `out_of_extent`}. | relationship + accepted_values |
| `ter_02` | `dem_resolution_m` is 10. `endpoint_draws` records how many sampled endpoint pairs produced the row and is at least 1. | accepted_values + range test |
| `ter_03` | `fresnel_clearance_pct_median <= 1.0`. | range test |
| `ter_04` | `knife_edge_db_median >= 0` and `knife_edge_db_iqr >= 0`. | range tests |
| `ter_05` | Land-cover shares along the path sum to 1 within 0.001. | singular test |
| `ter_06` | `hata_db` is present for every link and computed with the urban or suburban variant according to the NLCD developed share at the endpoints; the variant used is stored. | not_null + accepted_values |

## Validation

| Id | Rule | Enforced by |
| --- | --- | --- |
| `val_01` | Hold-out splits never place a node pair in both train and test. | pytest on the split function |
| `val_02` | Every headline number in `docs/results.md` names the asset or notebook cell that produced it. | reviewer checks |
| `val_03` | The residual variogram's nugget, sill, range and nugget-to-sill ratio are written to `analytics.variogram_result` before any kriging; kriging runs only when the ratio is below 0.8, and the skip is recorded as a row with `kriged = false`. | pytest on the guard function; singular test that no kriged grid exists for a run with ratio >= 0.8 |
