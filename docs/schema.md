# Warehouse schema

Every table and view in the warehouse that another agent or a person reads, with its grain (what one row is), its owner and where the data comes from. The plan's data model in `docs/PLAN.md` says what will exist; this file says what does. `warehouse-eng` keeps it current and the reviewer checks it on any PR that changes a schema.

Schemas, in pipeline order: `raw` (loaders write it, documented by ingest-eng in `ingester/README.md`), `staging` (views and seeds), `core` (incremental tables: one row per position delivery in `fct_node_position`, and the link-hour grain in the link models still to come), `features` (terrain per link-hour), `analytics` (marts). Sections for `core`, `features` and `analytics` are added here as their tables appear.

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
- Owner: warehouse-eng. Consumers: `fct_rx_observation` (next unit).
- Source: `raw.mqtt_message`, every packet type. A low byte of 0 in `origin_low_byte` or `gateway_low_byte` reads as 255, since firmware sends a relayer whose low byte is 0 as `relay_node` 0xFF. Coordinates and `hop_limit` are left out: positions are `stg_position`'s, and `hop_limit` never decides anything.

Columns: `delivery_key`, `snapshot_id`, `received_at`, `topic`, `modem_preset`, `seen_public`, `seen_meshstl`, `decode_status`, `origin_pseudonym`, `origin_low_byte`, `gateway_pseudonym`, `gateway_low_byte`, `packet_id`, `relay_node`, `via_mqtt`, `rx_time` (stored, never used to order), `rx_snr`, `rx_rssi`, `channel`, `portnum`, `payload_length`, `decoder_version`. Each is described in `dbt/models/staging/stg_packet.yml`.

Worked example (link-rules case c40): packet 40900 from `c40-r` reached `c40-g` once by radio and arrived as two deliveries, under the public broker's root at 10:59:50 and the MeshSTL broker's at 11:00:10. `stg_packet` keeps the 10:59:50 delivery, so the receipt falls in the 10:00 hour.

### stg_position (view)

- Grain: one raw Position or MapReport row, (`snapshot_id`, `delivery_key`); a delivery in two snapshots has a row in each.
- Owner: warehouse-eng. Consumers: `fct_node_position`.
- Source: `raw.mqtt_message` where `position_source` is set. A Position at 0, 0 has its coordinates set to null and `position_zero_mapped` true (`pos_03`). Only a row with both coordinates and not mapped has a position; any other row has null `precision_bits`, `position_quality`, `cell_width_i`, half-widths and `precise_source` (`pos_01`).

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

- Grain: one position delivery, `delivery_key`, whose stored value lies inside the MSA (`pos_02`; at 32 bits the raw fix, not the cell centre, Casey, PR #66, Q3). When several snapshots hold the delivery, its values come from the copy first by `received_at`, then snapshot id, and `seen_public` and `seen_meshstl` are true when any copy carried them, so `precise_source` reads `seen_meshstl` on any copy (`pos_04`; this reading awaits Casey, PR #69).
- Owner: warehouse-eng. Consumers: `int_link_window` (next unit), which reads only its `usable` rows (`lnk_02`).
- Source: `stg_position` rows with both coordinates, clipped with `st_within` against `stg_msa_boundary`. Every precision is kept.
- Columns: `delivery_key`, `snapshot_id`, `received_at`, `node_pseudonym`, `position_source`, `latitude_i`, `longitude_i`, `precision_bits`, `position_quality`, `cell_width_i`, `half_width_ns_m`, `half_width_ew_m`, `seen_public`, `seen_meshstl`, `precise_source`, as in `stg_position` except the three flags, which read every copy.
- Incremental: a run reads positions at or after the latest `received_at` already in the table, and every position of a snapshot not yet in it. Each delivery it touches is rebuilt from its new copies and its row already in the table, so a later copy can add a broker flag and an earlier copy replaces the row; an incremental run ends where a full refresh would. Rows added to a snapshot that is already built, which only the synthetic fixtures can do, need `--full-refresh`.
