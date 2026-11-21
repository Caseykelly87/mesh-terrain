# Roadmap

The work queued for the next phase, written down when this phase closed at release v0.1.0 (Casey, issue #55 comment 7051712349, Q3 (a): this file is the one list; a GitHub issue is opened only when a unit is briefed). Each line gives the purpose, what must happen first, and the owning agent. `docs/STATUS.md` says where things stand now, including the known issues several lines below close.

Nothing here is scheduled. Casey picks the order when the next phase starts.

## Drive test and analysis (Phase 4)

- Drive-test plan: replace passive public data with a controlled measurement, three boards of known transmit power, antennas and heights, logging every packet sent (`docs/findings.md`, What would answer the question). Prerequisites: Casey's research report, kept outside the repository; a planning ADR (routes, boards, logging, privacy of the drive positions); terrain tiles for the routes. Owner: lead for the ADR, then ingest-eng and terrain-rf-eng.
- Analysis design question: a case study of the engine on the capture's 8 usable pairs, or the drive test's paths. Prerequisites: none; asked before analytics-eng starts. Owner: lead, to Casey.
- Negative-SNR decision: how receipts decoded below the noise floor enter the observed loss (ADR 0006, addition A). Prerequisites: none; decided before Phase 4. Owner: lead, to Casey.
- Retain flag and the decoder's 0, 0 Position: record MQTT's retain flag per delivery and land a Position at 0, 0 as no position, with a decoder version bump. Prerequisites: before any new capture. Owner: ingest-eng, warehouse-eng reviewing the column.
- MeshSTL operators' agreement to any further capture. Prerequisites: none. Owner: lead and Casey.
- Overlapping captures: a second capture that overlaps an existing one in time comes to Casey as a question before it is loaded. Prerequisites: a second capture. Owner: lead.

## Engine (terrain-rf-eng)

- `position_uncertainty_m`: a new definition for the engine's input layers (ADR 0008, Q3 (a)), with the INTERFACE change. Prerequisites: before any corridor run. Owner: terrain-rf-eng, Casey decides INTERFACE.
- Export rule, manifest 1.4.0: the manifest records each input point layer's `mesh_terrain:positions` marker, and the export asset refuses any run that is not all synthetic, or whose manifest lacks the field (Casey's sentence in STATUS). Prerequisites: none. Owner: terrain-rf-eng for the engine, maps-eng for the export, lead and Casey for a new `prv_` contract rule.
- `--run-id` for `engine.prepare`: the drive test needs many runs per tile. Prerequisites: none. Owner: terrain-rf-eng.
- DSM height ceiling: drop lidar points more than N m above the DEM, N chosen from the tallest real buildings, with the count dropped per window. Prerequisites: before any corridor DSM build; a new DSM snapshot id and an ADR 0006 amendment, Casey's decision. Owner: terrain-rf-eng.
- Shared terrain cache, built once per tile and keyed by snapshot fingerprints and code version (Casey's option (b) conditions). Prerequisites: an INTERFACE change for the manifest's cache key. Owner: terrain-rf-eng.
- Corridor EPT checks: each other lidar project the corridors need gets the null-transform check before its corridors are built. Prerequisites: before corridor DSM builds. Owner: terrain-rf-eng.
- Metro DEM projection: the engine refuses a DEM tile not in EPSG:26915 until `dem_metro_10m` is decided. Prerequisites: none. Owner: terrain-rf-eng.
- Run-config template header: it says receivers include a real relay end even with `--no-real-receivers`. Prerequisites: none. Owner: terrain-rf-eng.
- Engine settings read a relative `SNAPSHOTS_DIR` from the working folder, unlike the pipeline and ingester settings, which read it from the checkout since PR #97. Prerequisites: none. Owner: terrain-rf-eng.
- P.1812 map check through `pipeline.common.snapshot` once the feature asset exists. Prerequisites: the features loader. Owner: terrain-rf-eng.
- ITU validation profiles in CI, once their licence is read. Prerequisites: none. Owner: terrain-rf-eng.
- Okumura-Hata suburban term checked against Hata 1980 or a textbook, if legitimate access exists; no purchase. Owner: terrain-rf-eng.
- Features loader into the warehouse. Prerequisites: none. Owner: terrain-rf-eng, warehouse-eng reviewing.

## Ingest (ingest-eng)

- No-gateway `skipped` fixture outcome, case c57 (Casey, issue #55 comment 7024471430). Prerequisites: none. Owner: ingest-eng with warehouse-eng; reviewer checks c57 independently; comes to Casey.
- `later_copies` with a null gateway: pin the case and make the Python match the SQL. Prerequisites: the c57 unit. Owner: ingest-eng.
- Rename retry in `pipeline/common/snapshot.py` for Windows access-denied errors. Prerequisites: none. Owner: ingest-eng.
- Logging test-order dependence (`cache_logger_on_first_use`). Prerequisites: none. Owner: ingest-eng.
- Mosquitto integration test for the capture command. Prerequisites: a broker service in CI (platform-eng). Owner: platform-eng, then ingest-eng.

## Warehouse (warehouse-eng)

- `analytics.mart_data_quality`, Phase 2's funnel in SQL, then the Phase 2 exit check. Prerequisites: none. Owner: warehouse-eng.
- Phase 2 Dagster dbt assets, settling which target a lead run uses. Prerequisites: none. Owner: warehouse-eng.
- Incremental link models: revisit earlier rows, or rebuild with `--full-refresh` in `make reproduce`. Owner: warehouse-eng.
- `dim_node` with relationship tests from the link models. Owner: warehouse-eng.
- Development builds as the test role from Dagster and the profile. Owner: platform-eng and warehouse-eng.
- Empty `dbt/dbt_packages`: a check refuses or refills it. Owner: warehouse-eng with platform-eng.
- `make reproduce`: rebuild raw and run dbt from the registered snapshots alone, with a same-counts-same-hashes test. Owner: ingest-eng, warehouse-eng, platform-eng.
- Receipt-level difference between the two link-rule implementations on the real capture: Python excludes 265 more receipts under `lnk_06` (18 388 against 18 123) yet keeps 311 more after `lnk_01`, `lnk_03` and `lnk_06` (296 248 against 295 937); kept link-hours and pairs agree (`docs/findings.md`). Prerequisites: none. Owner: ingest-eng and warehouse-eng, through a fixture case that reproduces it.

## Contract and decisions for Casey

- Impossible coordinates, a contract question for Casey, not a fix: should a latitude outside plus or minus 90 or a longitude outside plus or minus 180 be treated as missing in staging, the way `lnk_06` treats impossible signal readings (Casey's ruling on PR #72)? The real build stopped on it: `dbt_utils_accepted_range_stg_position_latitude_i__900000000___900000000` found 1 row. Owner: lead, to Casey; then warehouse-eng.
- `lnk_02` quotes ADR 0003's broadcast rule in the next contract PR. Owner: lead; Casey approves.
- `ing_04`'s row names its check as the code does (`ing_04_decode_rate`). Owner: lead, same PR.
- Whether a pytest test can ever count as a rule's test. Casey rules when a rule needs it. Owner: lead.
- 21 contract rules still on `PENDING` get their dbt test or asset check. Owner: warehouse-eng, ingest-eng, analytics-eng per rule.

## Platform upkeep (platform-eng)

- CI runs the warehouse tests in `tests/test_dbt_targets.py` and the dbt fixture build in its dbt-build job.
- CI check: a PR that changes the contract must add or change an ADR.
- Test-role revokes name each grant's grantor.
- `tests/test_db_role.py` dry-run test also catches `-h127.0.0.1` and `--host=`.
- `make nuke` names the project `mesh-terrain` itself, so in a clone with another `COMPOSE_PROJECT_NAME` it would act on the main stack's volumes; read the project name instead.
- `web/` joins mypy's strict file list.
- Pinned ITM and Py1812 source archives, with notices, attached to a release, builds pointed at them.
- GitHub Actions pinned by commit SHA.
- Pre-commit hooks installed in the main checkout.
- PDAL image whose version matches its tag, or Casey accepts 2.8.4.
- `make check-definitions` moves to `dg check defs` at the next Dagster upgrade.
- Dagster daemon heartbeat restarts explained or gone.
- Warehouse image's archived Debian sources go when upstream moves.
- Object store image: RustFS if the pinned MinIO build disappears; S3 at Phase 6.

## Deliverables (maps-eng)

- MapLibre site, PMTiles and the export asset (Phase 5), after the export rule.
- ArcGIS Pro script and layout, once a licence exists.
- The coverage figure's darkest colour has low contrast on a dark page theme (PR #95).
