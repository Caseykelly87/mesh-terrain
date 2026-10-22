# Development plan

## Objective

Answer one question with a number: how much of the variation in observed Meshtastic link quality across the St. Louis metro is explained by terrain-based propagation prediction, and what explains the rest. No hardware, no driving; every input is public.

Hypothesis: a bare-earth terrain model (free-space plus knife-edge diffraction, then ITM) will underpredict loss in developed areas, with an RMSE around 15 dB on direct links at 915 MHz; adding land-cover shares and building density along the path as covariates will reduce the unexplained error materially, and the remaining residual will show little spatial structure (a nugget-dominated variogram). Published studies of ITM at these frequencies in cities report 10 to 23 dB RMSE, so an 8 dB result would be a surprise, not the target.

Scope held fixed: St. Louis MSA bounding box; US 915 MHz LongFast preset; one-hop links (last relayer to gateway) only for validation.

## Two constraints of the public broker that shape everything

Verified against Meshtastic's own documentation and discussions before Phase 0:

1. The public broker sets `hop_limit` to 0 on every default-key packet and leaves `hop_start` as sent. A rule of the form `hop_start = hop_limit` identifies nothing. What a gateway measures (`rx_rssi`, `rx_snr`) is always the last RF hop into that gateway. So the analysable link is (last relayer, gateway), not (original sender, gateway). Firmware 2.6 and later carries the relayer's low id byte in `MeshPacket.relay_node`; whether the broker preserves it is measured in the Phase 1 spike. Fallback source of direct links: NeighborInfo packets, which list a node's directly heard neighbours with SNR.
2. The public broker forwards default-key positions only at 10 to 16 bits of precision; 16 bits is about plus or minus 364 m. Endpoint positions are therefore boxes, not points. Consequences: the 1 m DEM adds nothing and is dropped; terrain profiles are sampled over the position box and prediction is reported as a distribution; validation favours links long enough that the box is a small fraction of the path. The Phase 1 spike also checks for a St. Louis regional broker or a community channel with a published key, either of which would bypass the filter.

Both constraints and the funnel they create are stated plainly in the write-up. Explaining why the public feed limits the analysis is part of the deliverable.

## Stack

| Layer | Tool |
| --- | --- |
| Language, env | Python 3.12, uv |
| Orchestration | Dagster 1.x with dagster-dbt, dagster-postgres, dagster-webserver |
| Transforms | dbt-core 1.8+ with dbt-postgres, dbt-utils, dbt-expectations |
| Warehouse | PostgreSQL 17 + PostGIS 3.5 (`postgis/postgis:17-3.5`), matching RDS for PostgreSQL 17; see ADR 0004 |
| Raw landing | MinIO locally, S3 in cloud; Parquet via pyarrow |
| MQTT ingest | paho-mqtt 2.x, meshtastic protobufs, cryptography |
| Raster | rasterio, rio-cogeo, numpy, py3dep or direct National Map downloads |
| Vector | geopandas 1.x, shapely 2.x, pyproj, h3 4.x, pyrosm |
| Propagation | numpy FSPL + ITU-R P.526 knife edge (own code); NTIA ITM library or SPLAT CLI |
| Statistics | pandas, statsmodels, scipy, scikit-gstat, PyKrige, scikit-learn (comparison only) |
| Web map | MapLibre GL JS, PMTiles via tippecanoe, GitHub Pages |
| Esri | ArcGIS Pro 3.x, arcpy, ArcGIS Online hosted feature layer |
| Quality | pytest, ruff, mypy, pre-commit, dbt tests, Dagster asset checks, GitHub Actions |
| Infra | Docker Compose; Terraform 1.x on AWS (S3, RDS Postgres, one EC2) in Phase 6 |
| Config, logging | pydantic-settings, structlog, psycopg 3 |

Deliberately not used: Airflow, PostGIS raster, Spark or Sedona, Great Expectations, Kubernetes.

## Architecture

Three lanes. The streaming lane (MQTT subscriber, raw Parquet) runs continuously and never mutates data. The batch lane (raster assets, PostGIS, dbt, terrain features, statistics) is Dagster assets rebuildable from the raw zone. The output lane (exports, MapLibre site, ArcGIS) reads export files only.

Boundaries: raw is append-only; terrain work in Python writes features, not rasters, to PostGIS; dbt owns every table a human queries; Python assets write only to `raw` and `features`.

## Data sources

| Source | Use | Notes |
| --- | --- | --- |
| Meshtastic public MQTT broker | packets with envelope (from, to, id, hop_limit, hop_start, relay_node, rx_time, rx_snr, rx_rssi, gateway, channel) and decoded Position, NodeInfo, Telemetry, NeighborInfo | rx values are measured at the uploading gateway for the last hop; hop_limit is rewritten to 0 by the broker; positions arrive at 10 to 16 bits (about plus or minus 364 m at 16); the same packet via several gateways is several receipts, keyed by (packet id, gateway) |
| USGS 3DEP | 1/3 arc-second (about 10 m) metro-wide | reproject once to EPSG:26915, store as COG; 1 m LiDAR is a listed next step only if a precise position source appears |
| NLCD | land-cover class at each end and along each path | nearest-neighbour resampling |
| OpenStreetMap | building footprints, roads for context | pyrosm from a Geofabrik extract; ODbL attribution |
| Census TIGER | MSA boundary as the AOI clip | committed as a seed |

Decryption: default-channel packets use AES-CTR with a publicly documented key and a nonce from packet id and sender id. Store ciphertext and decoded payload both.

Antenna gain and transmit power are unknown. Per-node offsets are random effects in the statistics.

## Data model

| Schema | Owner | Tables (grain) |
| --- | --- | --- |
| `raw` | ingest-eng | `mqtt_message` (one per MQTT delivery), `decoded_packet` (one per delivery, with decode_status), `dem_tile`, `nlcd_tile`, `osm_extract` (one per file with checksum) |
| `staging` | warehouse-eng (views) | `stg_packet` (per packet id and gateway), `stg_position`, `stg_nodeinfo`, `stg_telemetry`, `stg_neighbor` |
| `core` | warehouse-eng (incremental) | `dim_node` (SCD2), `fct_node_position` (with precision_bits and the box half-width in metres), `fct_rx_observation` (one per receipt, with `relayer_node_hash` resolved from relay_node or NeighborInfo and `link_source` in {relay_node, neighbor_info}), `int_link_window` (per relayer, gateway, hour: median snr and rssi, n packets, both position boxes, LINESTRING between box centres, distance_m, bearing_deg, position_uncertainty_m) |
| `features` | terrain-rf-eng | `link_terrain` (per link-hour, aggregated over sampled endpoint positions: LOS share, Fresnel clearance median and range, obstruction, knife-edge dB median and range, NLCD histogram, building count and area), `link_prediction` (fspl_db, diffraction_db median and interquartile range, itm_db median and interquartile range, hata_db) |
| `analytics` | warehouse-eng and analytics-eng (H3 via the h3-pg extension in SQL) | `mart_link_validation`, `mart_hex` (H3 r8), `mart_node_effect`, `mart_data_quality` (daily funnel), `model_result`, `variogram_result` |

The unit of analysis is the link-hour, not the packet, and the link is last relayer to gateway. Rules are in `docs/data-quality-contract.md`.

## Phases

### Phase 0: Foundation (week 1)

Exit: `docker compose up` brings up PostGIS, MinIO, Dagster; CI green on an empty pipeline; ITM spike decided and recorded in `docs/adr/0002-propagation-model.md`.

Repo layout:

```
mesh-terrain/
  pyproject.toml, uv.lock, .pre-commit-config.yaml, Makefile
  docker-compose.yml, .env.example
  ingester/     pipeline/     dbt/     terrain/
  notebooks/    web/          arcgis/  infra/
  tests/        docs/
```

Tasks: uv project and tooling; Compose services and init SQL; dbt project and profile; Dagster Definitions with DbtCliResource; GitHub Actions (lint, types, tests, dbt build with service container); ITM spike time-boxed to three hours; MSA boundary seed.

### Phase 1: MQTT ingestion (week 2)

Exit: ingester ran unattended 72 hours; hourly Parquet lands; loader fills `raw.mqtt_message`; decode and freshness asset checks visible.

Tasks: paho-mqtt subscriber with buffer, flush and reconnect; Parquet partitioning by received date and hour; MinIO writes via pyarrow S3 filesystem; Dagster sensor on the bucket prefix; loader with COPY and ON CONFLICT DO NOTHING; decode asset with decrypt and portnum decoding; fixtures per portnum; asset checks; replay runbook.

Phase 1 spike, first three days, written up as `docs/adr/0003-direct-link-and-position-source.md` and decided by Casey: on 48 hours of St. Louis traffic, measure (a) the share of packets carrying a non-zero `relay_node` and whether it resolves to a known node, (b) the volume of NeighborInfo packets and how many distinct (neighbour, reporter) pairs they yield, (c) the distribution of `precision_bits` on positions, (d) whether any St. Louis regional broker or community channel with a published key exists and what precision it carries. The ADR picks the link source and the position source and records the expected link-hour yield.

### Phase 2: dbt transforms and data quality (week 3)

Exit: `dbt build` passes with at least 40 tests; a few hundred link-hours exist; `mart_data_quality` shows the daily funnel.

Tasks: staging views; `dim_node` snapshot with keyed-hash pseudonymisation; `fct_node_position` with precision_bits and box half-width; `fct_rx_observation` resolving the relayer per ADR 0003 and applying the position-window rule; `int_link_window` with geometry, medians and position_uncertainty_m; H3 cell assignment in SQL via the `h3-pg` extension (added to the Postgres image by platform-eng; RDS supports it); tests per the contract; Dagster dbt assets on a daily schedule.

### Phase 3: Terrain and propagation (weeks 4 and 5)

Exit: `features.link_terrain` and `features.link_prediction` populated for every link-hour; terrain functions tested on synthetic DEMs; one notebook shows a real profile with LOS, Fresnel ellipse and obstruction and the spread across sampled endpoints.

Tasks: `dem_metro_10m`, `nlcd_metro`, `osm_buildings` assets; `terrain/` functions (profile with earth curvature k = 4/3 using windowed rasterio reads and `scipy.ndimage.map_coordinates` for sampling, los_clear, fresnel_clearance_pct, fspl_db, knife_edge_db per P.526 with Bullington, hata_db per Okumura-Hata for 150 to 1500 MHz with urban and suburban variants, itm_db wrapper); endpoint sampling: for each link-hour draw N positions (default 16) uniformly inside each endpoint's precision box, compute every quantity per draw, store median and interquartile range; feature asset partitioned by day; synthetic-DEM tests; knife-edge versus ITM sanity comparison.

Memory rule: never read a DEM whole. Every profile reads one window covering the path's bounding box plus a margin.

### Phase 4: Validation analytics (week 6)

Exit: marts built; model results written; headline numbers in `docs/results.md`.

Tasks: baselines (distance-only, FSPL, FSPL + knife-edge, Okumura-Hata, ITM) with RMSE, MAE, bias, R squared on identical rows and a node-pair hold-out; mixed-effects model with random intercepts per relayer and gateway and the position_uncertainty_m term as a covariate or weight; variogram of the mixed-model residuals with the nugget-to-sill ratio logged and stored in `variogram_result`; kriging of those residuals (this is regression kriging, since the trend is already removed) to a 500 m grid only when the nugget-to-sill ratio is below 0.8, otherwise the step is skipped and the write-up states that residuals carry no usable spatial structure; H3 r8 mart with sparse-cell mask; sensitivity table (antenna heights 1 m and 5 m, endpoint draws 4 and 64, at least 10 packets, links over 3 km only).

### Phase 5: Outputs (week 7)

Exit: public MapLibre URL; public ArcGIS Online web map; README readable in five minutes.

Tasks: export asset (GeoParquet, COG, GeoPackage); tippecanoe PMTiles; MapLibre page with three toggles and profile on click for 400 links; GitHub Pages deploy on tag; arcpy script, `.aprx`, `.lyrx`, PDF layout, hosted feature layer; `docs/writeup.md` under 1500 words; `docs/interview-notes.md`.

The arcpy script is never imported from the project's uv environment and is not a Dagster asset. It runs on Windows through ArcGIS Pro's own interpreter (`propy.bat`) with a clean environment, reads the exported GeoPackage, and is documented as a manual step with its last-run date.

### Phase 6: Cloud deploy (week 8)

Exit: `terraform apply` from a clean account stands up the stack; nightly schedules run; monthly cost stated in README.

Tasks: Terraform modules s3 (with a CORS rule allowing GET and HEAD with the Range header from the site origin, since PMTiles depends on HTTP range requests), rds (db.t4g.micro, PostGIS and h3-pg), ec2 (t3.small with Compose), iam (instance role, OIDC for Actions), ssm; Dagster schedules (ingester health every 15 min, dbt nightly, features nightly, exports weekly); state in S3 with DynamoDB lock; fmt, validate, tflint, plan on PRs.

## Cut order if time is short

1. Phase 6 entirely (keep a Terraform stub and a README note).
2. ITM; ship FSPL + knife-edge and Hata as the headline models.
3. Kriging; keep the H3 map and the variogram numbers.
4. Endpoint sampling; use box centres with position_uncertainty_m as a covariate.

Never cut: the data-quality contract and tests, the link-hour grain, the mixed-effects result, the ArcGIS deliverable.

## Risks

| Risk | Mitigation |
| --- | --- |
| `relay_node` is stripped by the broker and NeighborInfo volume is low, so one-hop links cannot be identified | ADR 0003 decides after the spike; last resort is to treat every receipt as an unknown-relayer link and model only gateway-side terrain, which is a weaker but still honest project |
| Position precision of plus or minus 364 m swamps terrain effects on short links | Endpoint sampling, uncertainty as a covariate, validation restricted to links over 3 km; a regional broker or published-key channel, if found, lifts the limit |
| Too few positioned link-hours in the metro | Measure in week 2; if under about 300 link-hours after two weeks, widen to Missouri and Illinois |
| ITM bindings do not build | SPLAT fallback; FSPL + knife-edge and Hata are legitimate headlines |
| Residual variogram is pure nugget | Expected and reported as a finding; kriging is skipped by rule, not by hand |
| Broker changes topics or fields | raw bytes kept; decoder pinned to the meshtastic package version |
| Node operators object to mapping | pseudonymised ids, aggregated public outputs, stated in README |
| Scope creep | the cut list and `docs/next-steps.md` are the fence |

## Headline outputs

1. RMSE of each model with n links and the hold-out method.
2. Which covariates are significant, their sign, whether building count matters after terrain.
3. Where the model is worst, as a map, with one explained example link and its profile.
4. The data funnel: ingested, decoded, direct, positioned, usable link-hours.
