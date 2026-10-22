# Status

This file says where the project actually stands. It is updated by the agent that finishes each unit of work and checked by the reviewer on every PR. It describes the present; history lives in git and `docs/adr/`.

## Current phase and last unit

Phase 0, in progress. Last unit: platform-eng, Phase 0 foundation (uv project, tooling, Makefile, Compose, dbt scaffold, Dagster Definitions, CI), PR #2. Before it: terrain-rf-eng ITM spike and ADR 0002 (accepted: option 1, the NTIA ITM library via ctypes), PR #1.

## Deviations from the plan still in effect

- The object store is `bitnamilegacy/minio` pinned by digest, not `minio/minio`. Upstream MinIO archived the community server and client in 2026 and withdrew the images and binaries from Docker Hub, quay.io and dl.min.io; the Bitnami legacy build of the last community release is the only pullable image. It is frozen and may be removed; Casey decided to keep it (known issues below). The plan's stack table still says MinIO; nothing else in the design depends on the vendor, since the raw zone is S3-shaped.
- The warehouse image installs `postgis_raster` as an extension because `h3_postgis` declares it as a required dependency. No table stores rasters; the plan's rule against PostGIS raster is about storage and still holds.
- h3-pg is built from `github.com/postgis/h3-pg` (the `zachasme/h3-pg` repository named in the plan was archived and moved there) at tag v4.2.3, commit a26630b8.
- ADR 0001 said PostgreSQL 16; the warehouse is PostgreSQL 17 with PostGIS 3.5 per ADR 0004, and the plan's stack table already says so.
- Note for every agent: the plan was revised on 2026-10-21 after verifying two public-broker behaviours (hop_limit rewritten to 0; positions limited to 10 to 16 bits). The link is relayer to gateway, the 1 m DEM is out of scope, and ADR 0003 is pending the Phase 1 spike. Read the section "Two constraints of the public broker" in docs/PLAN.md before any Phase 1 or Phase 2 work.

## Known issues and skipped steps

- ArcGIS deliverable deferred: no ArcGIS Pro licence yet, so the arcpy script, layout and hosted feature layer wait until the rest of the pipeline passes its exit criteria. Owner: maps-eng, who builds the script untested against the exported GeoPackage. Closes when a licence exists and the manual smoke run is recorded.
- The Debian variant of `postgis/postgis:17-3.5` is still bullseye, which both Debian and the PostgreSQL apt project have moved to archive hosts; the warehouse Dockerfile rewrites the apt sources in its builder stage and pre-empts h3-pg's `pgindent` step (ADR 0004). Owner: platform-eng. Closes when the upstream image moves to a newer Debian and the rewrite can go.
- `astral-sh/setup-uv` is pinned to the exact tag v10.3.1 because the action publishes no major tag after v7; bumps are manual. Owner: platform-eng. Closes if the action resumes major tags.
- Object store image kept by Casey's decision: `bitnamilegacy/minio:2025.7.23-debian-12-r5@sha256:6dabb4a2088c9a79908de3bc05f4586c23ad2182c8908e7e3acbf61c1467fb20` and, for the bootstrap, `bitnamilegacy/minio-client:2025.7.21-debian-12-r3@sha256:73bd39f7899a0cef12b8dd5df13aa93a3ed1aaa44236542442e9ac76819ac158`. If either disappears from Docker Hub, switch to RustFS (`rustfs/rustfs:1.0.3`): RustFS claims a MinIO-compatible S3 API; whether `mc` and the bootstrap work against it has not been tried. The change would be the `minio` service definition in `docker-compose.yml` (image, data path, health check URL) and possibly the `mc` image used by `docker/minio/bootstrap.sh`; nothing in the pipeline changes because the raw zone is S3-shaped. Decision and options in ADR 0004. Owner: platform-eng. Closes at Phase 6 when S3 becomes the raw zone.
- The MinIO console on port 9001 is the reduced post-2025 object browser, not the old full console. Owner: platform-eng. Closes at Phase 6 with the line above.
- Pre-commit hooks are not installed on Casey's clone. Owner: platform-eng. Closes when `uv run pre-commit install` has been run there.
- GitHub Actions are pinned by tag, not by commit SHA, so a moved tag would change what runs. Owner: platform-eng. Closes when the four `uses:` lines carry SHAs with the tag in a comment.

## Environment and configuration notes

- Git identity: Casey Kelly, set at repo level. Automatic co-author trailers are off.
- Run `make` targets from Git Bash on Windows. The Makefile exports `.env` into every recipe and sets `UV_SYSTEM_CERTS=true`. `make up` waits through `docker/wait-for-stack.sh`, which fails when a long-running service exited or restarted or a one-shot (`dagster-migrate`, `minio-bootstrap`) exited non-zero.
- The Compose project name is fixed to `mesh-terrain`; `docker compose -p mesh-terrain ...` from any folder reaches the same containers and volumes.
- Ports on the host: warehouse 5433 (5432 is taken by another project on Casey's machine), object store API 9000, console 9001, Dagster 3000. Inside Compose the dagster containers use `warehouse:5432` and `minio:9000`.
- Warehouse image: `postgis/postgis:17-3.5` pinned by digest sha256:01a6a70e41e6c4467c8f55f6063555ed72db2d6662cd0d571040d42eadaeb6f6 (PostgreSQL 17.5, PostGIS 3.5.2, Debian bullseye) plus h3-pg built at commit a26630b8 (tag v4.2.3); matches RDS for PostgreSQL 17 (ADR 0004).
- Databases on the one Postgres server: `mesh_terrain` (warehouse, schemas `raw`, `staging`, `core`, `features`, `analytics`, extensions `postgis` 3.5.2, `postgis_raster` 3.5.2, `h3` 4.2.3, `h3_postgis` 4.2.3, plus the base image defaults `postgis_topology` 3.5.2, `postgis_tiger_geocoder` 3.5.2, `fuzzystrmatch` 1.2) and `dagster` (run storage, tables created by the `dagster-migrate` one-shot service). User `mesh`, password `mesh` locally.
- Bucket: `mesh-terrain-raw`, created by the `minio-bootstrap` one-shot service. Credentials `minio` / `minio12345` locally.
- Env var names (see `.env.example`): `WAREHOUSE_HOST`, `WAREHOUSE_PORT`, `WAREHOUSE_USER`, `WAREHOUSE_PASSWORD`, `WAREHOUSE_DB`, `DAGSTER_PG_DB`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `MINIO_API_PORT`, `MINIO_CONSOLE_PORT`, `S3_ENDPOINT_URL`, `RAW_BUCKET`, `DAGSTER_PORT`, `LOG_FORMAT`. `pipeline/common/settings.py` is the only reader; connection and credential fields have no defaults.
- Dagster image base: `python:3.12-slim-bookworm`, project installed with uv from `uv.lock`, dbt packages installed at build, manifest parsed at code-location load. The `libitm.so` build stage from ADR 0002 is the next platform-eng unit.
- h3-pg argument order: `h3_latlng_to_cell(point(longitude, latitude), resolution)`; the point's x is longitude. The PostGIS overload takes an SRID 4326 geometry. Function names in 4.2.3 use `latlng`, not `lat_lng`.
- On Casey's machine Norton re-signs HTTPS. uv works because of `UV_SYSTEM_CERTS`; Python needs `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` in `.env` pointing at `C:/Users/Casey/.certs/ca-bundle.pem` (README, "Running on Windows"). Containers and CI are unaffected.
- dbt packages: dbt_utils 1.4.1, dbt_expectations 0.10.10 (metaplane), which pulls dbt_date 0.21.0; `dbt/package-lock.yml` is committed.
- ITM spike images: `docker build --target itm -t itm-spike docs/adr/0002-itm-spike` then `docker run --rm itm-spike` prints the flat-path loss; `--target splat` builds the SPLAT comparison image. Both need network access to apt and to codeload.github.com.

## Interfaces agreed between agents, not yet in code

- terrain-rf-eng to platform-eng: per ADR 0002 (accepted), the Dagster image gets a build stage that compiles NTIA ITM commit `183ad95b` into `/usr/local/lib/libitm.so` (recipe in `docs/adr/0002-itm-spike/Dockerfile`) and copies only that file into the runtime stage. The 151.76 dB spike figure came from base image `python@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70` and `g++ 12.2.0-14+deb12u1`. `terrain/itm.py` will load it from that path. The Dagster image is based on `python:3.12-slim-bookworm` (docker/dagster/Dockerfile).

## Next three units of work

1. platform-eng: `libitm.so` build stage in the Dagster image per ADR 0002.
2. warehouse-eng or lead: MSA boundary seed under `dbt/seeds/` (the remaining Phase 0 task).
3. ingest-eng: Phase 1 spike (48-hour capture, relay_node and precision measurement, regional broker search) and draft of `docs/adr/0003-direct-link-and-position-source.md`, after Phase 0 exit criteria pass.
