# Development plan

Restated 2026-11-08 after Casey rescoped the project: batch analysis on dated, immutable snapshots, plus a reusable terrain-aware path-loss engine. There is no continuous or scheduled ingestion, now or planned.

## Objective

Answer one question with a number: how much of the variation in observed Meshtastic link quality across the St. Louis metro is explained by terrain-based propagation prediction, and what explains the rest. No hardware, no driving; every input is public.

The observed data is a single capture from 2026-10-22 to 2026-10-25 (snapshot `meshtastic-capture-2026-10-22`, ADR 0005). Nothing is collected on a schedule.

A second deliverable comes out of answering the question: a propagation engine that takes a terrain snapshot, transmitter sites and receiver points and writes predictions, a coverage raster and a manifest, from one command and a config file. It is built to be reusable; no downstream user is named anywhere in this repo. Its contract is `docs/INTERFACE.md`.

Hypothesis: a bare-earth terrain model (free-space plus knife-edge diffraction, then ITM) will underpredict loss in developed areas, with an RMSE around 15 dB on direct links at 915 MHz; adding path clutter (building and canopy height from the 1 m layers) as covariates will reduce the unexplained error materially, and the remaining residual will show little spatial structure (a nugget-dominated variogram). Published studies of ITM at these frequencies in cities report 10 to 23 dB RMSE, so an 8 dB result would be a surprise, not the target.

Scope held fixed: the St. Louis MSA; US 915 MHz LongFast preset; one-hop links (last relayer to gateway) only for validation. The area is the MSA; the Missouri plus Illinois widening trigger of ADR 0003 was retired on 2026-11-08.

## Two constraints of the public broker that shape everything

Verified against Meshtastic's own documentation and the ADR 0003 spike:

1. The public broker sets `hop_limit` to 0 on every default-key packet and leaves `hop_start` as sent. A rule of the form `hop_start = hop_limit` identifies nothing. What a gateway measures (`rx_rssi`, `rx_snr`) is always the last RF hop into that gateway. So the analysable link is (last relayer, gateway), not (original sender, gateway). Firmware 2.6 and later carries the relayer's low id byte in `MeshPacket.relay_node`; the spike found it on 87 percent of radio deliveries and ADR 0003 resolves links from it. Fallback: NeighborInfo packets, which the MeshSTL broker forwards.
2. The public broker forwards default-key positions only at 10 to 16 bits of precision; 16 bits is about plus or minus 364 m. Endpoint positions are therefore boxes, not points. Consequences: terrain profiles are sampled over the position box and prediction is reported as a distribution; validation favours links long enough that the box is a small fraction of the path. The 1 m layers do not sharpen the endpoints; they sharpen the clutter along the path, and they are built only where links are (Phase 3).

Both constraints and the funnel they create are stated plainly in the write-up. Explaining why the public feed limits the analysis is part of the deliverable.

## Operating model: snapshots

Every input is a snapshot: a folder with an id, a capture date, a source URL, a licence and a sha256 manifest of its files, recorded in `raw.snapshot` under contract rule `snp_01`. A refresh is a new snapshot id, never an overwrite. Re-running the pipeline against the same snapshot ids reproduces the same artifacts. Snapshots live on the host under `C:/Users/Casey/mesh-terrain-data/snapshots/<id>/`, outside every Docker volume, so `make nuke` cannot touch them. A run writes into `<id>.partial/` and renames it only after the manifest is written. The one way a finished snapshot changes is Casey's approved removal of a listed set of files (`ing_02`), such as the real-id archive after the reproduction gate: the manifest is rewritten after the change and the snapshot README records the date, the list and the approval. They are not in git or LFS and are never published.

Snapshot sources, each started by one documented make target or command and never on a schedule:

| Source | Snapshot | Licence | Notes |
| --- | --- | --- | --- |
| Meshtastic MQTT capture (public broker and, with Casey's approval per run, the MeshSTL broker) | `meshtastic-capture-2026-10-22`, both brokers, 2026-10-22 to 2026-10-25 | none published; captured from public brokers under their terms, held privately | ADR 0005: allowlisted fields, HMAC pseudonyms, no raw bytes, no text, no names. Not irreplaceable: if lost, its figures are reproducible only by a new capture under a new id. Precise MeshSTL positions are never published. |
| USGS 3DEP 1/3 arc-second DEM | dated editions of the six tiles n39w090 to n40w092 | public domain | metro-wide profiles for every link; pin the edition, since `/current/` changes |
| USGS 3DEP 1 m DEM | dated TNM editions, test tile and link corridors only | public domain | pair each DEM with the EPT project of the same collection (MO_SaintLouis_2017 for the core) |
| USGS 3DEP Entwine point tiles (EPT), read, not kept | the 1 m first-return DSM as a COG, test tile and corridors only | public domain | PDAL reads EPT over each window and writes the DSM; no point cloud is stored. The snapshot holds the DSM, the EPT URL, the bounds and the sha256 of the PDAL pipeline file. Streamed volume 6.5 bytes a point at 3 to 34 points per square metre (`docs/measurements-2026-11-08.md`) |
| Meta and WRI canopy height v2 | tiles over the test tile and corridors | CC BY 4.0 on the AWS registry, DINOv3 licence in the paper; both recorded in the manifest, attribution for both in the README and the map credits | 2018 to 2020 imagery, 40 to 68 percent leaf-on; lidar over the MSA is all leaf-off (`docs/measurements-2026-11-08.md`) |
| OpenStreetMap | Geofabrik extract, dated | ODbL | building footprints, roads for context |
| Census TIGER | MSA boundary | public domain | committed as a seed |

NLCD is dropped. Path clutter comes from DSM minus DEM and the canopy layer; the Okumura-Hata urban or suburban variant is chosen from the building share near each endpoint (`ter_06`).

TTN Mapper is not a source. Its dumps are gone (the last, from 2020, is unarchived), its API is closed, and its maintainer said in December 2025 the dataset will not be released; it was also LoRaWAN at other spreading factors from rooftop gateways. It is recorded as a limitation in the write-up. If Casey obtains a St. Louis extract from the maintainer, it may serve as a calibration source for the clutter term only, under its own privacy rule, never as a validation set.

## Stack

| Layer | Tool |
| --- | --- |
| Language, env | Python 3.12, uv |
| Orchestration | Dagster 1.x with dagster-dbt, dagster-postgres, dagster-webserver; asset graph for lineage only, no schedules, no sensors |
| Transforms | dbt-core 1.8+ with dbt-postgres, dbt-utils, dbt-expectations |
| Warehouse | PostgreSQL 17 + PostGIS 3.5 (`postgis/postgis:17-3.5`); see ADR 0004 |
| Snapshots | host folders with sha256 manifests; Parquet via pyarrow |
| Derived artifacts | engine runs write to a host output folder; exports and PMTiles go to MinIO locally, S3 in cloud |
| MQTT capture | paho-mqtt 2.x, meshtastic protobufs, cryptography; on demand, ADR 0005 |
| Raster | rasterio, rio-cogeo, numpy, PDAL (DSM from EPT), direct National Map and S3 downloads |
| Vector | geopandas 1.x, shapely 2.x, pyproj, h3 4.x, pyrosm |
| Propagation | numpy FSPL + ITU-R P.526 knife edge (own code); NTIA ITM library (ADR 0002); ITU-R P.1812 via Py1812, pinned to a git commit (ADR 0006); Okumura-Hata |
| Statistics | pandas, statsmodels, scipy, scikit-gstat, PyKrige, scikit-learn (comparison only) |
| Web map | MapLibre GL JS, PMTiles via tippecanoe, GitHub Pages |
| Esri | ArcGIS Pro 3.x, arcpy, ArcGIS Online hosted feature layer |
| Quality | pytest, ruff, mypy, pre-commit, dbt tests, Dagster asset checks, GitHub Actions |
| Infra | Docker Compose, every service `restart: "no"`; Terraform 1.x on AWS in Phase 6 |
| Config, logging | pydantic-settings, structlog, psycopg 3 |

Deliberately not used: Airflow, PostGIS raster, Spark or Sedona, Great Expectations, Kubernetes, NLCD.

## Architecture

Three lanes. The snapshot lane (captures and downloads) produces immutable folders on the host when a person starts it. The batch lane (loaders, dbt, terrain, the engine, statistics) is Dagster assets started by hand or by `make reproduce`, rebuildable from the snapshots. The output lane (exports, MapLibre site, ArcGIS) reads export files only. Nothing runs unless started.

Boundaries: snapshots are read-only once their manifest exists; terrain work in Python writes features, not rasters, to PostGIS, and writes rasters only as engine artifacts; dbt owns every table a human queries; Python assets write only to `raw`, `features` and the artifact store.

## Data model

| Schema | Owner | Tables (grain) |
| --- | --- | --- |
| `raw` | ingest-eng | `mqtt_message` (one per stored delivery per snapshot, allowlisted fields, decode status, `seen_public`, `seen_meshstl`), `mqtt_copy_count` (per snapshot, hour, topic, gateway pseudonym), `mqtt_dropped_count` (per snapshot, hour, topic class, reason), `snapshot` (one per registered snapshot) |
| `staging` | warehouse-eng (views) | `stg_packet` (per packet id and gateway, deduplicated across snapshots), `stg_position` (Position and MapReport, with `position_source`), `stg_nodeinfo`, `stg_telemetry`, `stg_neighbor` |
| `core` | warehouse-eng (incremental) | `dim_node` (SCD2, keyed on the pseudonym), `fct_node_position` (with precision_bits and the box half-width in metres), `fct_rx_observation` (one per receipt, with the relayer pseudonym resolved per ADR 0003 and `link_source`), `int_link_window` (per relayer, gateway, hour: median snr and rssi, n packets, modem preset, both position boxes, LINESTRING between box centres, distance_m, bearing_deg, position_uncertainty_m) |
| `features` | terrain-rf-eng | `link_terrain` (per link-hour, aggregated over sampled endpoint positions: LOS share, Fresnel clearance median and range, obstruction, knife-edge dB median and range, clutter height statistics along the path, building share and canopy share near each endpoint), `link_prediction` (`<model>_db_median` and `<model>_db_iqr` for each of fspl, knife_edge, hata, itm, p1812, plus `hata_variant`) |
| `analytics` | warehouse-eng and analytics-eng (H3 via the h3-pg extension in SQL) | `mart_link_validation`, `mart_hex` (H3 r8), `mart_node_effect`, `mart_data_quality` (funnel), `model_result`, `variogram_result` |

The unit of analysis is the link-hour, not the packet, and the link is last relayer to gateway. Rules are in `docs/data-quality-contract.md`.

## Phases

The sequence is the original one with the smallest changes that fit the snapshot model: Phase 1 becomes a reduction of the existing capture rather than a service, Phase 3 gains the engine milestone, Phase 6 loses its schedules.

### Phase 0: Foundation

Exit: `docker compose up` brings up PostGIS, MinIO, Dagster; CI green; ITM spike decided (ADR 0002); the spike and its ADRs 0003 and 0005 accepted. Done except PR #13.

### Phase 1: Snapshot and reduction

Exit: the decoder passes its fixture tests; snapshot `meshtastic-capture-2026-10-22` exists with README and manifest; the reproduction gate passes (every ADR 0003 link figure, including 560 against 177 per broker, reproduces from the pseudonymised Parquet) and Casey has confirmed it; the real-id archive is deleted after his yes; `raw.mqtt_message` and the count tables are loaded; the on-demand capture command is documented with the 2026-10-22 run as the worked example.

Tasks (ingest-eng): `raw` tables and loader (`COPY`, `ON CONFLICT DO NOTHING` on the three keys); decoder test-first on synthetic fixtures under `tests/fixtures/mqtt/` (none exist; built, never recorded); batch reduction of the gzipped public archive and the in-window MeshSTL capture into the snapshot folder, Parquet by hour; the gate; `docs/adr/0003-spike/capture.py` brought to the ADR 0005 design in place, not moved, as the capture command (broker, topic list, END_AT, MAX_RAW_BYTES, snapshot id; allowlist and pseudonymisation in memory; manifest at exit; no restart, no marker, no schedule); README.

### Phase 2: dbt transforms and data quality

Exit: `dbt build` passes with at least 40 tests; the link-hours of the capture exist in `int_link_window`; `mart_data_quality` shows the funnel.

Tasks (warehouse-eng): staging views, cross-snapshot dedup in `stg_packet`; `dim_node` keyed on the pseudonym, no hashing in dbt; `fct_node_position` with `position_source`; `fct_rx_observation` resolving the relayer per ADR 0003; `int_link_window` with the stationarity rule (`lnk_02`), geometry, medians, modem preset and position_uncertainty_m; H3 in SQL via `h3-pg`; tests per the contract; Dagster dbt assets, run by hand.

### Phase 3: Terrain and the engine

Exit, metro: `features.link_terrain` and `features.link_prediction` populated for every link-hour from the 1/3 arc-second DEM; terrain functions tested on synthetic DEMs; one notebook shows a real profile with LOS, Fresnel ellipse, obstruction and the spread across sampled endpoints.

Exit, engine milestone: one documented command, from a clean checkout and the Docker image, produces: a terrain and DSM build for the test tile; a prediction from a two-site fixture to a 500-point receiver fixture as GeoParquet; a derived coverage COG; a calibration residual report against a synthetic observations fixture; and a manifest that a script in a scratch directory can load without the database. The full test tile (x72y429, decided by Casey on 2026-11-08: it holds one both-ends pair entirely and the gateway ends of the two busiest pairs; x71y428 holds none) is a documented manual acceptance run: about 2.2 to 2.9 GB of EPT streamed through PDAL and not kept, about 0.5 GB kept (DSM COG 150 to 250 MB, DEM tile 160 to 330 MB, one canopy tile 163 to 220 MB). CI runs the same command on a committed 1 km sub-tile fixture. Interface rules (`docs/INTERFACE.md`): frequency is a run parameter; transmitter and receiver profiles are named config records, with a mesh node and a site gateway shipped; receivers are a GeoParquet point layer; transmitter sites are a caller-supplied point layer; calibration is a separate labelled artifact that never mutates predictions; one end-to-end command from a config file.

Tasks (terrain-rf-eng): ADR 0006 (P.1812 via Py1812 beside ITM behind one function with a `model` parameter; FSPL, knife-edge and Okumura-Hata as validation baselines; the ITU map files installed by a documented step, never committed); `dem_metro_10m` from pinned editions; 1 m DEM, PDAL DSM and canopy assets for the test tile (the 10 km USGS 1 m tile x72y429; in the USGS naming xAyB covers easting A to A+1 and northing B-1 to B in 10 km units, so y names the northern edge) and for corridors of 500 m half-width around the observed links, meaning the union of the both-ends-positioned pairs (6 public, 7 MeshSTL; the gateway-only pairs have no relayer position and so no path), on demand, about 75 km2, 2 to 6 GB of EPT streamed and about 300 MB of DSM kept; clutter height = max(DSM minus DEM, canopy where nonzero), with the leaf-off lidar against leaf-on capture stated in ADR 0006; `terrain/` functions (profile with earth curvature k = 4/3 using windowed reads, los_clear, fresnel_clearance_pct, fspl_db, knife_edge_db per P.526 with Bullington, hata_db with the variant from the building share, itm_db and p1812_db wrappers); endpoint sampling (N draws, default 16, uniform in each box; median and interquartile range per quantity); validation runs use the `mesh_node` profile at both ends unless ADR 0006 decides otherwise, with heights varied in the Phase 4 sensitivity table; the engine command, profiles, fixtures and manifest; synthetic-DEM tests.

Memory rule: never read a raster whole. Every profile reads one window covering the path's bounding box plus a margin.

### Phase 4: Validation analytics

Exit: marts built; model results written; headline numbers in `docs/results.md`.

Tasks (analytics-eng): baselines (distance-only, FSPL, FSPL + knife-edge, Okumura-Hata, ITM, P.1812) with RMSE, MAE, bias, R squared on identical rows and a node-pair hold-out; mixed-effects model with random intercepts per relayer and gateway, modem preset as a stratification term, and position_uncertainty_m as a covariate or weight; variogram of the residuals with the nugget-to-sill ratio stored; regression kriging to a 500 m grid only when the ratio is below 0.8, otherwise skipped by rule; H3 r8 mart with sparse-cell mask; sensitivity table (antenna heights 1 m and 5 m, endpoint draws 4 and 64, at least 10 packets, links over 3 km only); the TTN Mapper limitation stated.

### Phase 5: Outputs

Exit: public MapLibre URL; public ArcGIS Online web map; README readable in five minutes.

Tasks (maps-eng): export asset (GeoParquet, COG, GeoPackage) with `prv_02` and `prv_03` applied and no precise MeshSTL position in any export; tippecanoe PMTiles; MapLibre page with three toggles and profile on click, with map credits carrying the USGS 3DEP, OpenStreetMap (ODbL) and Meta and WRI canopy height (CC BY 4.0 and the DINOv3 licence) attributions; GitHub Pages deploy on tag; arcpy script, `.aprx`, `.lyrx`, PDF layout, hosted feature layer; `docs/writeup.md` under 1500 words, describing the data as a single capture from 2026-10-22 to 2026-10-25; `docs/interview-notes.md`.

The arcpy script runs on Windows through ArcGIS Pro's own interpreter (`propy.bat`), reads the exported GeoPackage, and is a documented manual step with its last-run date.

### Phase 6: Cloud deploy (optional)

Exit: `terraform apply` from a clean account stands up the stack and one documented command runs the pipeline against the registered snapshots; monthly cost stated in README. No schedules, no sensors, no retention sweeps.

Tasks (platform-eng): Terraform modules s3 (CORS for PMTiles range requests), rds (db.t4g.micro, PostGIS and h3-pg), ec2 (t3.small with Compose), iam (instance role, OIDC for Actions), ssm; state in S3 with DynamoDB lock; fmt, validate, tflint, plan on PRs.

## Cut order if time is short

1. Phase 6 entirely (keep a Terraform stub and a README note).
2. Corridor 1 m layers; keep the test tile build as the engine acceptance.
3. Kriging; keep the H3 map and the variogram numbers.
4. Endpoint sampling; use box centres with position_uncertainty_m as a covariate.

Never cut: the data-quality contract and tests, the link-hour grain, the mixed-effects result, the engine milestone, the ArcGIS deliverable.

## Risks

| Risk | Mitigation |
| --- | --- |
| The capture's link-hours are too few for the mixed model | Usable count: 62 to 89 both-ends-positioned link-hours across 6 to 7 pairs in the spike. Gateway-only link-hours have no distance and do not enter the model. Report with confidence intervals; a further capture under a new snapshot id is a Casey decision, never automatic |
| Position precision of plus or minus 364 m swamps terrain effects on short links | Endpoint sampling, uncertainty as a covariate, validation restricted to links over 3 km |
| The snapshot is lost | It is not irreplaceable; its figures are reproducible only by a new capture under a new snapshot id, recorded in ADR 0003 |
| ITM bindings or Py1812 do not build | SPLAT fallback for ITM; P.1812 needs two ITU map files installed by hand and fails loudly without them; FSPL + knife-edge and Hata are legitimate headlines |
| Lidar DSM is leaf-off while the capture is leaf-on | Clutter height takes the larger of DSM minus DEM and the canopy layer; stated as a limitation |
| Residual variogram is pure nugget | Expected and reported as a finding; kriging is skipped by rule |
| Decoder bug after the real-id archive is deleted | Fixtures per packet type are the replay mechanism; `decoder_version` on every row lets faulty rows be excluded |
| Node operators object to mapping | pseudonymised ids, aggregated public outputs, no precise MeshSTL position published, stated in README |
| Scope creep | the cut list and `docs/next-steps.md` are the fence |

## Headline outputs

1. RMSE of each model with n links and the hold-out method.
2. Which covariates are significant, their sign, whether clutter height matters after terrain.
3. Where the model is worst, as a map, with one explained example link and its profile.
4. The data funnel: captured, decoded, direct, positioned, usable link-hours.
5. The engine: one command, one config, one manifest; the acceptance run on the test tile.
