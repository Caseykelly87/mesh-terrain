# Roadmap

The work queued for the next phase at release v0.1.0 (Casey, issue #55 comment 7051712349, Q3 (a): this file is the one list; a GitHub issue is opened only when a unit is briefed). Each line gives the purpose, what must happen first, and the owning agent; where a section heading names an owner, that agent owns every line under it, and a line that names no prerequisite has none. `docs/STATUS.md` says where things stand now, including the known issues several lines below close.

Nothing here is scheduled. Casey picks the order when the next phase starts.

## Drive test and analysis (Phase 4)

- Drive-test plan: replace passive public data with a controlled measurement, three boards of known transmit power, antennas and heights, logging every packet sent (`docs/findings.md`, What would answer the question). Prerequisites: a PLAN scope change, Casey's decision (PLAN's objective says no hardware and no driving); Casey's research report, kept outside the repository; a planning ADR (routes, boards, logging, privacy of the drive positions); terrain tiles for the routes. Owner: lead for the ADR, then ingest-eng and terrain-rf-eng.
- Analysis design question: a case study of the engine on the capture's 8 usable pairs, or the drive test's paths. Prerequisites: none; asked before analytics-eng starts. Owner: lead, to Casey.
- Negative-SNR decision: how receipts decoded below the noise floor enter the observed loss (ADR 0006, addition A). Prerequisites: none; decided before Phase 4. Owner: lead, to Casey.
- Retain flag and the decoder's 0, 0 Position: record MQTT's retain flag per delivery as a boolean, with a contract row for it and ADR 0005's per-delivery time rule saying how retained deliveries are timed; land a Position at 0, 0 as no position, with a decoder version bump. Prerequisites: before any new capture. Owner: ingest-eng, warehouse-eng reviewing the column.
- MeshSTL operators' agreement to any further capture. Prerequisites: none. Owner: lead and Casey.
- Overlapping captures: a second capture that overlaps an existing one in time comes to Casey as a question before it is loaded. Prerequisites: a second capture. Owner: lead.

## Engine (terrain-rf-eng)

- `position_uncertainty_m`: the half-diagonal sum Casey chose (ADR 0008, Q3 (a)) for the engine's input layers, with the `lnk_05` contract wording and the 40 fixture values ingest-eng and warehouse-eng review (STATUS, Interfaces), and the INTERFACE change. Prerequisites: before any corridor run. Owner: terrain-rf-eng with ingest-eng; Casey decides INTERFACE and the contract wording.
- The export-rule unit (Casey, issue #44 comment 6933558042, item 4): manifest 1.4.0 (1.2.0 went to the rectangle draws, Casey, PR #73, Q2 (b), and 1.3.0 to the GPS error floor, Casey, issue #55 comment 7042577618, item 3) records each input point layer's `mesh_terrain:positions` marker, with the engine and INTERFACE change (terrain-rf-eng); the same sentence goes into maps-eng's stop list and a new contract `prv_` rule, through the lead and Casey. His sentence, verbatim: "An engine run's own artifacts are never exported or published as they are unless the manifest records every input point layer as synthetic. Results of other runs reach any public output only through the export asset, with prv_02 and prv_03 applied and no precise MeshSTL position. Coverage rasters and maps from any run whose sites include a real gateway are never exported or published, even after those transforms. The export asset refuses anything else, including a run whose manifest lacks the field." The marker is already stamped (PR #48 and ingest-eng's layers); the engine reads it to admit point layers (PR #73), and the manifest does not record it yet.
- terrain-rf-eng: a `--run-id` option for `engine.prepare`, since the drive test will need many runs (Casey, issue #44 comment 7046959252, item 2).
- DSM height ceiling: drop lidar points more than N m above the DEM, N chosen from the tallest real buildings, with the count dropped per window. Prerequisites: before any corridor DSM build; a new DSM snapshot id and an ADR 0006 amendment, Casey's decision. Owner: terrain-rf-eng.
- terrain-rf-eng: the shared terrain cache, option (b), built once per tile (Casey, issue #55 comment 6962700456, item 3, verbatim: "yes to option (b), on these conditions: the cache is keyed by snapshot fingerprints and code version, each run manifest records the cache key it used, the cache lives under [the data folder], and nothing existing is deleted without a listed set and my yes. If (b) does not meet one of these, bring it back."; the bracket replaces the folder's path, which is in the owner's local notes). The manifest field is an INTERFACE change and comes to Casey.
- Corridor EPT checks: each other lidar project the corridors need gets the null-transform check before its corridors are built. Prerequisites: before corridor DSM builds. Owner: terrain-rf-eng.
- Metro DEM projection: the engine refuses a DEM tile not in EPSG:26915 until `dem_metro_10m` is decided. Prerequisites: none. Owner: terrain-rf-eng.
- terrain-rf-eng: the run-config template's header comment, which says receivers include a real relay end even with `--no-real-receivers` (Casey, issue #44 comment 6960753910, item 2).
- Engine settings read a relative `SNAPSHOTS_DIR` from the working folder, unlike the pipeline and ingester settings, which read it from the checkout since PR #97. Prerequisites: none. Owner: terrain-rf-eng.
- P.1812 map check through `pipeline.common.snapshot` once the feature asset exists. Prerequisites: the features loader. Owner: terrain-rf-eng.
- ITU validation profiles in CI, once their licence is read. Prerequisites: none. Owner: terrain-rf-eng.
- Okumura-Hata suburban term checked against Hata 1980 or a textbook, if legitimate access exists; no purchase. Owner: terrain-rf-eng.
- Features loader into the warehouse (Casey, issue #55 comment 6959184944). Prerequisites: none. Owner: terrain-rf-eng, warehouse-eng reviewing.

## Ingest (ingest-eng)

- No-gateway `skipped` fixture outcome, case c57 (Casey, issue #55 comment 7024471430). Prerequisites: none. Owner: ingest-eng with warehouse-eng; reviewer checks c57 independently; comes to Casey.
- `later_copies` with a null gateway: pin the case and make the Python match the SQL. Prerequisites: the c57 unit, by the lead's ordering, since both extend the same case table. Owner: ingest-eng.
- ingest-eng: the rename-retry change to `pipeline/common/snapshot.py` (Known issues; Casey, issue #44, "after this run").
- Logging test-order dependence (`cache_logger_on_first_use`). Prerequisites: none. Owner: ingest-eng.
- Mosquitto integration test for the capture command. Prerequisites: a broker service in CI (platform-eng). Owner: platform-eng, then ingest-eng.

## Warehouse (warehouse-eng)

- `analytics.mart_data_quality`, Phase 2's funnel in SQL, then the Phase 2 exit check. Prerequisites: none. Owner: warehouse-eng.
- Phase 2 Dagster dbt assets, settling which target a lead run uses. Prerequisites: none. Owner: warehouse-eng.
- Incremental link models: revisit earlier rows, or rebuild with `--full-refresh` in `make reproduce`. Owner: warehouse-eng.
- `dim_node` with relationship tests from the link models. Owner: warehouse-eng.
- Development builds as the test role from Dagster and the profile. Owner: platform-eng and warehouse-eng.
- Empty `dbt/dbt_packages`: a check refuses or refills it. Owner: warehouse-eng with platform-eng.
- `make reproduce` (Casey, PR #35, 2026-11-15): it rebuilds raw and runs dbt build from the registered snapshots alone, the rebuild path `ing_02` relies on, with a test that a rebuild gives the same row counts and content hashes. Owners: ingest-eng for the raw rebuild, warehouse-eng for dbt, platform-eng for the target. It also rewords the stale Makefile help and stub message (the `help` text for `reproduce` and the `reproduce` target's stub message).
- Receipt-level difference between the two link-rule implementations on the real capture: Python excludes 265 more receipts under `lnk_06` (18 388 against 18 123) yet keeps 311 more after `lnk_01`, `lnk_03` and `lnk_06` (296 248 against 295 937); kept link-hours and pairs agree (`docs/findings.md`). Prerequisites: none. Owner: ingest-eng and warehouse-eng, through a fixture case that reproduces it.

## Contract and decisions for Casey

- Impossible coordinates, a contract question for Casey, not a fix: should a latitude outside plus or minus 90 or a longitude outside plus or minus 180 be treated as missing in staging, the way `lnk_06` treats impossible signal readings (Casey's ruling on PR #72)? The real build stopped on it: `dbt_utils_accepted_range_stg_position_latitude_i__900000000___900000000` found 1 row. Owner: lead, to Casey; then warehouse-eng.
- `lnk_02` quotes ADR 0003's broadcast rule in the next contract PR. Owner: lead; Casey approves.
- `ing_04`'s row names its check as the code does (`ing_04_decode_rate`). Owner: lead, same PR.
- Whether a pytest test can ever count as a rule's test. Casey rules when a rule needs it. Owner: lead.
- 15 contract rules still on `PENDING` in `tests/test_contract.py` get their dbt test or asset check: `ing_02`, `prv_01` to `prv_03`, `ter_01` to `ter_07`, `val_01` and `val_03`. Owner: warehouse-eng, ingest-eng, analytics-eng per rule.

## Platform upkeep (platform-eng)

- platform-eng: run the tests in `tests/test_dbt_targets.py` that need the warehouse in CI's dbt-build job, where a warehouse is up (Known issues, PR #60). The same job also runs the dbt fixture build, `tests/test_dbt_fixture_build.py` and `tests/test_dbt_raw_schema_var.py` (requested of platform-eng in PR #69).
- platform-eng: a CI check that fails when a PR changes `docs/data-quality-contract.md` without adding or changing a file under `docs/adr/` (Casey, issue #55 comment 6959592431, item 8, "Queue a small unit after B1").
- Test-role revokes name each grant's grantor.
- `make nuke` names the `mesh-terrain` project on purpose, so a stray `COMPOSE_PROJECT_NAME` in the environment cannot point it at another project's volumes (`docker/nuke.sh`); in a clone with another `COMPOSE_PROJECT_NAME` it would act on the main stack's volumes instead of the clone's. Make it refuse when `COMPOSE_PROJECT_NAME` names another project, keeping the hard-coded name.
- `web/` joins mypy's strict file list.
- platform-eng: the exact pinned ITM and Py1812 source archives, with their notices, attached to a release on this repo, with the builds pointed at those URLs and the same sha256 pins (Casey, PR #32, 2026-11-15).
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
