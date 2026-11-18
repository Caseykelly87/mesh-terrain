# Warehouse schema

Every table and view in the warehouse that another agent or a person reads, with its grain (what one row is), its owner and where the data comes from. The plan's data model in `docs/PLAN.md` says what will exist; this file says what does. `warehouse-eng` keeps it current and the reviewer checks it on any PR that changes a schema.

Schemas, in pipeline order: `raw` (loaders write it, documented by ingest-eng in `ingester/README.md`), `staging` (views and seeds), `core` (incremental tables: one row per position delivery in `fct_node_position`, one per receipt in `fct_rx_observation`, and one per link-hour in `int_link_window`), `features` (terrain per link-hour), `analytics` (marts). Sections for `core`, `features` and `analytics` are added here as their tables appear.

Development copies. A dbt target is a named set of connection settings in `dbt/profiles.yml`. Only the target `prod`, which the lead runs, builds the schemas this file describes. Every other target builds the same tables in schemas named with its own suffix: the default target `dev_wh` (Track B, `make check`) builds `staging_dev_wh`, `core_dev_wh` and `analytics_dev_wh`, and `dev_ta` (Track A, `make check DBT_DEV_TARGET=dev_ta`) the same names ending `_dev_ta`. Their raw schemas, `raw_dev_wh` and `raw_dev_ta`, hold no tables; the models that read raw are built on a development target only by `tests/test_dbt_fixture_build.py`, from synthetic fixtures in a throwaway `test_fixture_<12 hex>` schema (`dbt/README.md`, Fixture build). For example, `make check` puts a copy of `stg_msa_boundary` in `staging_dev_wh.stg_msa_boundary` and leaves `staging.stg_msa_boundary` alone. The copies are for tests; nothing reads them as results. `dbt/README.md` has the details.

Conventions that hold everywhere below: geometry columns are stored in SRID 4326 (longitude and latitude in degrees) and any area, distance or azimuth is computed after reprojecting to EPSG:26915 (UTM zone 15N, metres) with `st_transform`, or after casting to `geography`. Names are `snake_case`.

## staging

### msa_boundary (seed)

- Grain: one chunk of the boundary's WKT text. Two rows today; concatenate them in `part` order to get the whole polygon.
- Owner: warehouse-eng.
- Source: Census TIGER/Line 2025 CBSA shapefile, feature GEOID 41180, converted by the recipe in `dbt/seeds/README.md`. Loaded by `dbt seed` from `dbt/seeds/msa_boundary.csv`.

| Column | Type | Meaning |
| --- | --- | --- |
| `geoid` | text | Census CBSA code, `41180` on every row. |
| `name` | text | Census name, `St. Louis, MO-IL`. |
| `source` | text | TIGER file name and vintage, `tl_2025_us_cbsa.zip (TIGER/Line 2025)`. |
| `part` | integer | Order of the chunk, starting at 1. |
| `geometry_wkt` | text | A chunk of the MultiPolygon WKT in EPSG:4326, longitude first. |

Why chunks: the WKT is 179,994 characters and dbt cannot load a CSV field over 131,072 characters. Splitting the text keeps the committed geometry identical to the Census file; the alternative, simplifying the polygon, is explained in the seed README.

### stg_msa_boundary (view)

- Grain: one row, the St. Louis metropolitan statistical area. A test fails the build if the count is anything else.
- Owner: warehouse-eng. Consumers: ingest-eng (the Phase 1 spike clips positions with it) and, through rule `pos_02` of the data-quality contract, every `core` model that carries a position.
- Source: `msa_boundary`, chunks joined with `string_agg`, parsed with `st_geomfromtext(..., 4326)`, forced to MultiPolygon. There is no repair step: the 2025 feature is valid as published, and a future vintage that is not fails `assert_msa_boundary_is_valid` instead of being fixed silently.

| Column | Type | Meaning |
| --- | --- | --- |
| `geoid` | text | Census CBSA code, `41180`. |
| `name` | text | Census name, `St. Louis, MO-IL`. |
| `geom` | geometry(MultiPolygon, 4326) | The boundary. 8,269 vertices, 20,943.9 km2 in EPSG:26915. |

Worked example, the question every consumer asks: is a point inside the metro?

```sql
select st_contains(geom, st_setsrid(st_point(-90.1848, 38.6247), 4326)) as inside
from staging.stg_msa_boundary;
```

That point is the Gateway Arch and the answer is `true`. `st_point` takes longitude first, then latitude. Swapped, the point has latitude -90.18, which is past the south pole and not a real location, so the answer is `false` with no error to warn you. The tests in `dbt/tests/assert_msa_boundary_*.sql` check this exact query, that the geometry is not null, that downtown Kansas City is outside, that the geometry is valid, that the SRID is 4326 and that the area is between 18,000 and 25,000 km2.

### stg_packet (view)

- Grain: one packet heard at one gateway, (`origin_pseudonym`, `packet_id`, `gateway_pseudonym`): of several copies, in one snapshot or across snapshots, the first by `received_at`, then snapshot id, then delivery key (`ing_06`). A row without `packet_id` or gateway is kept per delivery key, its copies in other snapshots collapsing into it. `delivery_key` is unique.
- Owner: warehouse-eng. Consumers: `fct_rx_observation`, through the ephemeral `int_rx_resolution`.
- Source: `raw.mqtt_message`, every packet type.
- Low bytes: `origin_low_byte` and `gateway_low_byte` hold 255 where raw holds 0, since firmware sends a relayer whose low byte is 0 as `relay_node` 0xFF, so a byte compared with `relay_node` must use the same value. Staging never holds 0 in a low-byte column: raw lands a `relay_node` of 0 as null, and `assert_stg_low_bytes_never_zero` fails on any 0 in `stg_packet` or `stg_neighbor` (Casey, issue #55, his message on PR #69, item 1 (b)).
- Readings: an `rx_snr` that is not a number (NaN) lands as null, since `lnk_06` treats an impossible value as missing (`assert_lnk_06_snr_not_a_number_is_missing`). Other readings are kept as raw holds them; `lnk_06`'s ranges are applied in `fct_rx_observation`.
- Left out: coordinates, which are `stg_position`'s, and `hop_start` and `hop_limit`, which no link rule reads. `lnk_01` decides a direct hop by `relay_node` equal to the sender's own low byte, not by hops (Casey's item 1 (a) on PR #69 asked whether any link rule reads either; none does).

Columns: `delivery_key`, `snapshot_id`, `received_at`, `topic`, `modem_preset`, `seen_public`, `seen_meshstl`, `decode_status`, `origin_pseudonym`, `origin_low_byte`, `gateway_pseudonym`, `gateway_low_byte`, `packet_id`, `relay_node`, `via_mqtt`, `rx_time` (stored, never used to order), `rx_snr`, `rx_rssi`, `channel`, `portnum`, `payload_length`, `decoder_version`. Each is described in `dbt/models/staging/stg_packet.yml`.

Worked example (link-rules case c40): packet 40900 from `c40-r` reached `c40-g` once by radio and arrived as two deliveries, under the public broker's root at 10:59:50 and the MeshSTL broker's at 11:00:10. `stg_packet` keeps the 10:59:50 delivery, so the receipt falls in the 10:00 hour.

### stg_neighbor (view)

- Grain: one neighbour listed in one NeighborInfo row, (`snapshot_id`, `delivery_key`, `neighbour_position`), in message order from 1.
- Owner: warehouse-eng. Consumers: `lnk_01`'s NeighborInfo resolution in `int_rx_resolution`, which reads a gateway's own reports (`reporter_pseudonym` equal to the gateway).
- Source: `raw.mqtt_message` rows with `neighbour_pseudonyms`, its four arrays unnested together. `neighbour_low_byte` holds 255 where raw holds 0, as in `stg_packet`. Each snapshot's copy of a report stays; `lnk_01` counts distinct neighbours, so copies change nothing.

Columns: `snapshot_id`, `delivery_key`, `neighbour_position`, `received_at`, `reporter_pseudonym` (the node that sent the report), `gateway_pseudonym` (the node that uplinked it), `neighbour_pseudonym`, `neighbour_low_byte`, `neighbour_snr` (dB), `neighbour_last_heard` (Unix seconds). Each is described in `dbt/models/staging/stg_neighbor.yml`.

### stg_position (view)

- Grain: one raw Position or MapReport row, (`snapshot_id`, `delivery_key`); a delivery in two snapshots has a row in each.
- Owner: warehouse-eng. Consumers: `fct_node_position`.
- Source: `raw.mqtt_message` where `position_source` is set. A Position at 0, 0 has its coordinates set to null and `position_zero_mapped` true (`pos_03`). Only a row with both coordinates and not mapped has a position; any other row has null `precision_bits`, `position_quality`, `cell_width_i`, half-widths and `precise_source` (`pos_01`). The mapping only ever meets a Position: the decoder lands no MapReport at 0, 0, since a MapReport with both coordinates zero carries no location and gets no `position_source` (`ingester/decoder.py`, `_map_report`, lines 266 to 268).

| Column | Type | Meaning |
| --- | --- | --- |
| `snapshot_id`, `delivery_key`, `received_at` | text, text, timestamptz | The raw row. |
| `node_pseudonym` | text | The node the position belongs to, the packet's origin. |
| `position_source` | text | `position` or `mapreport`. |
| `seen_public`, `seen_meshstl` | boolean | Which brokers carried the delivery. |
| `position_zero_mapped` | boolean | True for a Position sent at 0, 0. |
| `latitude_i`, `longitude_i` | integer | 1e-7 degrees as sent: the cell centre for 1 to 31 bits, the raw value at 32. |
| `precision_bits` | bigint | As sent. |
| `position_quality` | text | `usable` 15 to 32 bits, `coarse` 12 to 14, `unusable` otherwise. |
| `cell_width_i` | bigint | 2^(32 - `precision_bits`), the cell's width on each axis in 1e-7 degrees; null at 0 bits and outside 0 to 32. |
| `half_width_ns_m`, `half_width_ew_m` | double precision | Half the cell's extent in metres on the WGS84 ellipsoid at the stored latitude (`pos_01`'s formula). |
| `precise_source` | boolean | `precision_bits` of 20 or more and carried by the MeshSTL broker (`pos_04`). |

A cell's edges are not stored; the link models compute them with the macro `cell_low_i` (`dbt/macros/positions.sql`): the low edge is the stored value minus half the width for 1 to 31 bits and the stored value at 32, and the cell is [low, low + width). Worked example: c01's gateway sent 385974272, -903970816 at 16 bits, so the width is 65536, the cell runs from 385941504 to 386007040 in latitude and from -904003584 to -903938048 in longitude, and the half-widths are 363.750 m north-south and 285.459 m east-west.

## core

### fct_node_position (incremental table)

- Grain: one position delivery, `delivery_key`, whose stored value lies inside the MSA (`pos_02`; at 32 bits the raw fix, not the cell centre, Casey, PR #66, Q3). When several snapshots hold the delivery, its values come from the copy first by `received_at`, then snapshot id, and `seen_public` and `seen_meshstl` are true when any copy carried them, so `precise_source` reads `seen_meshstl` on any copy (`pos_04`; Casey, issue #55 comment 6985802330, item 7: "(a), any copy in any snapshot"). A MeshSTL position at 0 bits that carries coordinates is also precise, Position or MapReport; PR #71 defines it: "a position carries coordinates when `latitude_i` and `longitude_i` are both present and `pos_03` does not map it to no position, so a report at 0, 0 is not flagged" (Casey, PR #71 comment 6989275630, item 1) (Casey, issue #55 comment 6989099568, item 2; PR #71: "or a position with `precision_bits` 0 that carries coordinates, Position or MapReport, is flagged `precise_source = true` exactly when `seen_meshstl` is true on its delivery").
- Owner: warehouse-eng. Consumers: `int_link_window`, through the ephemeral `int_usable_position`, which reads only its `usable` rows (`lnk_02`).
- Source: `stg_position` rows with both coordinates, clipped with `st_within` against `stg_msa_boundary`. Every precision is kept.
- Columns: `delivery_key`, `snapshot_id`, `received_at`, `node_pseudonym`, `position_source`, `latitude_i`, `longitude_i`, `precision_bits`, `position_quality`, `cell_width_i`, `half_width_ns_m`, `half_width_ew_m`, `seen_public`, `seen_meshstl`, `precise_source`, as in `stg_position` except the three flags, which read every copy.
- Incremental: a run reads positions at or after the latest `received_at` already in the table, and every position of a snapshot not yet in it. Each delivery it touches is rebuilt from its new copies and its row already in the table, so a later copy can add a broker flag and an earlier copy replaces the row; an incremental run ends where a full refresh would. Rows added to a snapshot that is already built, which only the synthetic fixtures can do, need `--full-refresh`.

### fct_rx_observation (incremental table)

- Grain: one receipt, `delivery_key`, the copy `stg_packet` keeps. A receipt is a packet a gateway heard by radio and uplinked, not its own, with a `relay_node`.
- Owner: warehouse-eng. Consumers: `int_link_window`, through the ephemeral link models below.
- What enters: a receipt whose last relayer resolves to exactly one known node (`lnk_01`), other than the gateway (`lnk_03`), with RSSI in [-150, 0) dBm and SNR in [-30, 20] dB, both present (`lnk_06`). The relayer is resolved by `relay_node`: equal to the sender's own low byte it is a direct hop; otherwise exactly one sender the gateway heard by radio in the 24 hours before the receipt, both edges included, has that byte. The gateway's own NeighborInfo reports in the same 24 hours resolve it when they list exactly one neighbour with the byte, counted across all of them. When both resolve, to different nodes, the receipt is dropped, a direct hop included (Casey's Q4); `link_source` says which method named the relayer. `hop_start` and `hop_limit` are never read.
- Key names: `tx_node_hash` (the relayer), `gateway_node_hash` and `hour` are the names `lnk_08` gives the link-hour key; they hold the same pseudonyms as `stg_packet`'s `origin_pseudonym` and `gateway_pseudonym`.

Columns: `delivery_key`, `snapshot_id`, `received_at`, `hour` (the clock hour in UTC that holds `received_at`, half-open), `tx_node_hash`, `gateway_node_hash`, `link_source` (`relay_node` or `neighbor_info`), `origin_pseudonym` (the sender), `packet_id`, `relay_node`, `rx_snr` (dB), `rx_rssi` (dBm), `modem_preset`, `seen_public`, `seen_meshstl`. Each is described in `dbt/models/core/fct_rx_observation.yml`.

Incremental: a run adds the receipts received after the latest `received_at` already in the table, and every receipt of a snapshot not yet in it. Resolution reads only the 24 hours before a receipt, so later data never changes a receipt already there; a new snapshot that adds deliveries before a receipt already in the table needs `--full-refresh`.

Worked example (link-rules case c06): `c06-g` heard `c06-r1` and `c06-r2` by radio, both with byte 0x51, and its own NeighborInfo report lists `c06-r1` alone with 0x51. A receipt with `relay_node` 0x51 has two candidates, so `relay_node` does not resolve it, and NeighborInfo does: `tx_node_hash` is `c06-r1` and `link_source` is `neighbor_info`.

### int_link_window (incremental table)

- Grain: one kept link-hour, (`tx_node_hash`, `gateway_node_hash`, `hour`) (`lnk_08`): the last relayer, the gateway and the clock hour.
- Owner: warehouse-eng. Consumers: terrain-rf-eng's `features` tables (one row per link-hour), the analytics marts.
- What is kept, in `lnk_04`'s order: each receipt of `fct_rx_observation` needs a position for each end (`lnk_02`); each end then gets its box for the hour; each receipt passes `lnk_05` with the box it uses (500 m to 60 000 m between box centres, both included, boxes not overlapping); the counted receipts must show one box per end (all four edges equal), or the hour is dropped and counted; then at least 3 counted receipts; then a median distance of at least 500 m.
- Boxes (`lnk_02`): an end is stationary at a receipt when it sent at least one usable position inside the MSA in the 7 days before it and every two of them are the same place (their cells overlap, or their stored values lie within `STATIONARY_FLOOR_M`, 40 m, of each other, pairwise and never chained). Its box is then the rectangle around the cells of those positions that hold no finer one. Otherwise it takes the cell of its position nearest in time within 30 minutes either side, the earlier on a tie. An end stationary at every receipt of the hour, whose positions from 7 days before the first receipt to the last are pairwise the same place, uses one box built from all of them for every receipt.
- Medians are `percentile_cont(0.5)` over the counted receipts, so an even count averages the two middle values.

Columns:

| Column | Type | Meaning |
| --- | --- | --- |
| `tx_node_hash`, `gateway_node_hash`, `hour` | text, text, timestamptz | The key (`lnk_08`). |
| `packet_count` | bigint | Counted receipts, at least 3. |
| `first_received_at`, `last_received_at` | timestamptz | The first and last counted receipt; `last_received_at` is the incremental watermark. |
| `median_snr_db`, `median_rssi_dbm` | double precision | Medians over the counted receipts. |
| `modem_preset` | text | The preset when every counted receipt names the same one, else null. |
| `distance_m` | double precision | Median WGS84 geodesic between the box centres, 500 to 60 000 m (`lnk_05`). |
| `bearing_deg` | double precision | Geodesic azimuth from the relayer centre to the gateway centre, degrees clockwise from north. |
| `position_uncertainty_m` | double precision | The larger half-width of each end's box, summed over the two ends (`lnk_05`). |
| `relayer_south_i`, `relayer_north_i`, `relayer_west_i`, `relayer_east_i` | bigint | The relayer box's half-open edges in 1e-7 degrees. |
| `relayer_merged` | boolean | True when the box is the rectangle around two or more cells the floor made one place. |
| `relayer_precision_bits` | bigint | The most precision among the cells the box covers. |
| `relayer_half_width_ns_m`, `relayer_half_width_ew_m` | double precision | `pos_01`'s half-widths; for a merged rectangle, from its own extent at its midpoint's latitude. |
| `relayer_precise_source` | boolean | Every cell the box covers was sent in a `pos_04` delivery among the positions `lnk_02` read for that end in that hour (D7). |
| `gateway_*` | as above | The same nine columns for the gateway box. |
| `geom` | geometry(LineString, 4326) | From the relayer box centre to the gateway box centre, in that order (`lnk_07`). |

Incremental: a run adds the link-hours after the latest `hour` already in the table. Data that changes that hour or an earlier one needs `--full-refresh`.

Worked example (link-rules case c21): `c21-r` sent a 16-bit cell on 22 October and, at 10:25 on 23 October, the 20-bit cell inside it. Its receipts at 10:10 and 10:20 alone would use the 16-bit cell and the 10:30 receipt the 20-bit one, two boxes, and the hour would be dropped. But `c21-r` is stationary at all three and the two cells overlap, so the hour's box is chosen over both: the 20-bit cell, which every receipt uses. The hour is kept with `relayer_precision_bits` 20 and `position_uncertainty_m` 22.734 m + 363.750 m = 386.485 m.

### Ephemeral link models (no table)

These are compiled into `fct_rx_observation`, `int_link_window` and their tests as common table expressions; none exists in the warehouse.

- `int_rx_resolution`: every packet a gateway uplinked, with `lnk_01`'s two methods, `direct_hop_contradicted` (Casey's Q4 drops, the count `lnk_01_direct_hop_contradicted`) and `drop_rule` (`lnk_01`, `lnk_03`, `lnk_06` or null).
- `int_usable_position`: the usable positions inside the MSA with their cell edges, `next_conflict_at` (the first later position of the node that is not the same place) and `finer_received_at` (when the node sent the finer positions the cell holds).
- `int_receipt_end_cell`, `int_receipt_end`: each end of each receipt, the cells it read and its own box.
- `int_link_hour_box`: each end's box for the hour, where it has one.
- `int_link_receipt`: each receipt with the boxes it uses, its distance and `drop_rule` (`lnk_02`, `lnk_05` or null).
- `int_link_hour`: every (relayer, gateway, hour) with a counted receipt and its `drop_rule` (`lnk_02`, `lnk_04`, `lnk_05` or null). The hours dropped by `lnk_02` are the ones `mart_data_quality` will count.
