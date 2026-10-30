# Data-quality contract

Every rule here has a dbt test or a Dagster asset check with the same identifier. A unit test in `tests/test_contract.py` parses this file and fails if a rule has no matching test. Change a rule only with an ADR and Casey's approval.

Identifiers are stable. Tests are named `assert_<identifier>` (dbt singular) or declared in the model YAML with `meta: {contract: <identifier>}`.

## Ingestion

| Id | Rule | Enforced by |
| --- | --- | --- |
| `ing_01` | Every raw MQTT delivery is stored once, keyed by `sha256(topic || payload_bytes)`. | unique test on `raw.mqtt_message.key` |
| `ing_02` | Raw rows are never updated or deleted. | no `UPDATE` or `DELETE` statement in any loader; reviewer checks |
| `ing_03` | Every raw row has a decode row with a status in {`ok`, `unknown_port`, `decrypt_failed`, `stripped`, `malformed`}. `stripped` means the broker forwarded the envelope header with no payload, so there was nothing to decode. | relationship + accepted_values on `raw.decoded_packet` |
| `ing_04` | Decode success rate over the trailing 24 hours is at least 90 percent. | Dagster asset check `decode_rate` |
| `ing_05` | The newest raw file is less than 15 minutes old while the ingester is expected to be running. | Dagster asset check `raw_freshness` |
| `ing_06` | Ordering uses `received_at` (our clock). `rx_time` is stored but never used as a key. | reviewer checks; `rx_time` absent from any `ORDER BY` or window |

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
| `prv_01` | No real identifier exists on disk (ADR 0005). | ingester test that every node, gateway and neighbour id is written as its HMAC pseudonym; singular test scanning information_schema for `node_id` columns in every schema |
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
