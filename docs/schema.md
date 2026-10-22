# Warehouse schema

Every table and view in the warehouse that another agent or a person reads, with its grain (what one row is), its owner and where the data comes from. The plan's data model in `docs/PLAN.md` says what will exist; this file says what does. `warehouse-eng` keeps it current and the reviewer checks it on any PR that changes a schema.

Schemas, in pipeline order: `raw` (loaders write it, documented by ingest-eng in `ingester/README.md`), `staging` (views and seeds), `core` (incremental tables at the link-hour grain), `features` (terrain per link-hour), `analytics` (marts). Sections for `core`, `features` and `analytics` are added here as their tables appear.

Conventions that hold everywhere below: geometry columns are stored in SRID 4326 (longitude and latitude in degrees) and any area, distance or azimuth is computed after casting to EPSG:26915 (UTM zone 15N, metres) or to `geography`. Names are `snake_case`.

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
- Source: `msa_boundary`, chunks joined with `string_agg`, parsed with `st_geomfromtext(..., 4326)`, forced to MultiPolygon. `st_makevalid` runs only if the parsed geometry is invalid; the 2025 feature is valid, so it did not run.

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

That point is the Gateway Arch and the answer is `true`. `st_point` takes longitude first, then latitude; swapping them puts the point in the Indian Ocean and the answer becomes `false`. The tests in `dbt/tests/assert_msa_boundary_*.sql` check this exact query, that downtown Kansas City is outside, that the geometry is valid, that the SRID is 4326 and that the area is between 18,000 and 25,000 km2.
