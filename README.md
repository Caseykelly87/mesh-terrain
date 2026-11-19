# mesh-terrain

How much of the variation in Meshtastic LoRa link quality across the St. Louis metro is explained by terrain, and what explains the rest. A batch pipeline takes a single Meshtastic MQTT capture from 2026-10-22 to 2026-10-25, models it in PostGIS with dbt and Dagster, derives terrain features from USGS 3DEP elevation and lidar data, and tests standard radio propagation models against observed link quality. Every input is a dated, immutable snapshot; nothing is collected on a schedule. A reusable path-loss engine comes out of the same work (`docs/INTERFACE.md`). The plan, data model and phases are in `docs/PLAN.md`; where the project actually stands is in `docs/STATUS.md`.

## Quick start

Requirements: Docker (Compose v2), GNU Make, [uv](https://docs.astral.sh/uv/) 0.13, Python 3.12 (uv installs it if missing).

```
make up      # builds and starts PostGIS with h3-pg, the object store and Dagster, waits for health checks,
             # then creates or updates the tests' database role once its two values are set
             # (make test-role; see Test role below)
make check   # ruff, mypy, make test-role, pytest, dbt deps and dbt build against the local stack
make down    # stops the stack and removes its containers; the data is kept
make nuke    # stops the stack and deletes every project volume, after you type "nuke"
```

`make up` and `make down` are the way to start and stop the stack; the one other start is `make rotate-owner-password`, which starts the warehouse alone (Credentials, below). Every Compose service has `restart: "no"`, so opening Docker Desktop or restarting the machine starts nothing; run `make up` again. Start with `make up`, not `docker compose up`, `docker start` or the Docker Desktop buttons, which skip `make check-credentials` and the health and exit-code checks in `docker/wait-for-stack.sh`. Stop with `make down`, which removes the containers so the next `make up` starts fresh containers.

The stack's data lives in Docker volumes, storage that Docker keeps apart from any container. Today there are two: `mesh-terrain_warehouse-data` holds the warehouse and Dagster databases, `mesh-terrain_minio-data` holds the object store bucket. `make down` leaves them in place, so the next `make up` finds the same tables and files. `make nuke` is the only target that deletes them, and it deletes every volume of the mesh-terrain project, including any added later. It lists the volumes, asks you to type `nuke`, and deletes nothing if you type anything else. `make nuke CONFIRM=nuke` skips the prompt, at a terminal too, so type it only when you mean it. With no terminal to ask on (CI, a script, a pipe) it refuses unless `CONFIRM=nuke` is on the make command line; a `CONFIRM` in your shell environment or in `.env` does not count. It also refuses, deleting nothing, if a container outside the stack still uses one of the volumes. After a nuke, `make up` recreates empty volumes with the schemas and the bucket, and `make check` reloads the dbt seeds. Snapshots (the MQTT capture, downloaded elevation and lidar tiles) are kept in folders on the host under `C:/Users/Casey/mesh-terrain-data/snapshots/`, never in these volumes, so a nuke cannot touch them.

`make up` copies `.env.example` to `.env` if it is missing, and then stops until you have generated the credentials in it (Credentials, below). Dagster is at http://localhost:3000, the warehouse at localhost:5433 (database `mesh_terrain`, user `mesh`, the owner of every schema), the object store API at localhost:9000 and its console at localhost:9001, each reachable from this machine only: every published port is bound to 127.0.0.1. `make fmt` formats; `make reproduce` will rebuild every asset from the registered snapshots once Phase 1 lands the loader.

### Credentials

`.env.example` holds placeholders for `WAREHOUSE_PASSWORD`, `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD`, and anyone can read them in this repository, so never keep them. `make up` and `make rotate-owner-password` first run `make check-credentials`, which refuses to go on while any of the three variables in `.env` is empty or equals any of the three placeholders, also when it is written in quotes or with spaces around it; the message names the variable, never a value. `make check-credentials` also runs on its own. This Git Bash command writes a new random value for each of the three straight into `.env`, without printing any; to change only some of them, list only those names after `in`:

```
for name in WAREHOUSE_PASSWORD MINIO_ROOT_USER MINIO_ROOT_PASSWORD; do sed -i "s|^${name}=.*|${name}=$(openssl rand -hex 24)|" .env; done
```

On a fresh volume Postgres takes `WAREHOUSE_PASSWORD` when it creates the database, and MinIO reads its root user and password from `.env` every time it starts. An existing warehouse keeps the password it was created with, so changing `WAREHOUSE_PASSWORD` later takes these steps, in this order:

1. `make down` stops every container; the volumes are kept.
2. Generate the new value in `.env` with the command above.
3. `make rotate-owner-password` starts the warehouse alone and sets the owner's password to the new value. It logs in through the container's local socket, which the image trusts without a password, the same way the warehouse's health check does, so it never needs the old password and cannot lock the owner out. The new value reaches psql inside the container by name, and nothing prints or logs it.
4. `make up` starts everything else. The Dagster containers and the object store take the new values from `.env` as they start, and the host commands (dbt, the loaders, the tests) read `.env` on every run, so nothing else needs restarting.

If the rotation stops partway, the owner keeps its old password: the change is a single statement in one transaction. Until it succeeds, anything that logs in over the network with the new value from `.env` is refused, Dagster's start in `make up` included. Rerun `make rotate-owner-password` once the cause is fixed (the socket login does not depend on either password), or put the old value back in `.env`.

To run the pre-commit hooks on every commit: `uv run pre-commit install`.

### Test role

The tests, and the dbt builds that `make check` and `make dbt-build` run, connect as a second role, named by `WAREHOUSE_TEST_USER` and `WAREHOUSE_TEST_PASSWORD`, never as `mesh`. It has no privilege at all on `raw`, `raw_dev_wh` or `raw_dev_ta`, so any write a test sends there fails with "permission denied" (`tests/test_db_role.py` sends each form), and it holds none on the real `staging`, `core`, `analytics` or `features` either. It may create schemas, which the tests' throwaway `test_load_` and `test_fixture_` schemas need, and it owns the development build schemas such as `staging_dev_wh`, because dbt replaces a view only as its owner. `make test-role` applies `docker/warehouse/test-role.sql` to the running warehouse; `make check` and `make dbt-build` run it first, and `make up` runs it once both values are set, so an existing volume gets the role as well as a new one. A new checkout can run `make up` before setting them; it says the role was not created, and the first `make check` with the values set creates it. Everything else connects as `mesh`: `make dbt-build-real`, `make dbt-debug`, `make load`, `make register-snapshot` and Dagster.

Only the main checkout's `.env` sets the two values; `.env.example` lists the names commented out, with placeholders, so a worktree's `.env` copied from it sets neither. A worktree never gets a copy of the main `.env`, which holds `NODE_ID_HMAC_KEY`. Instead, a check in a worktree starts with a line that exports the two lines of the main checkout's `.env` and nothing else (`grep` reads the file and passes on only lines that start with those two names, and no file is written), joined with `&&` to the make command in one Git Bash command. They must run in the same shell: an export lasts only as long as its shell, and an agent's tool starts a new shell for each call, so an export run on its own is gone before the make command starts. For example:

```
while IFS= read -r line; do export "$line"; done < <(grep -E '^WAREHOUSE_TEST_(USER|PASSWORD)=' /c/Users/Casey/Desktop/GEO/mesh-terrain/.env | tr -d '\r') && make check DBT_DEV_TARGET=dev_ta
```

A value from the shell wins over any `.env` that still sets one, and `make check`, `make dbt-build` and `make test-role` stop before running anything when either is missing. In the main checkout itself the `.env` lines are enough. CI sets both in the workflow, with values for its throwaway warehouse only.

Casey asked that the two values never appear in a transcript or log (issue #55 comment 7017280290, item 2). What holds: the export line prints nothing, so never add `echo`, `env`, `printenv`, `set -x` or the like around it; no make recipe expands either value, so `make -n` and make's echo of a recipe show only the names; `make dbt-debug` runs as `mesh`, because `dbt debug` prints the user it connects as; no test message names the role, and `tests/conftest.py` replaces both values in every pytest report, so a failure shows neither at `-vv`, with `-l` or `--showlocals`, in a chained error or in captured output; and `test-role.sql` prints neither value, raises only fixed messages naming the failed step, and turns off statement, CONTEXT, slow-statement and sampled logging for its own transaction. What can still show a value:

1. the role's name, never the password, in the server log's line for a refused login (`password authentication failed for user`, or `role ... does not exist`), written whenever anything logs in as the role with a wrong or stale password or before the role exists. Postgres has no setting that leaves the user out. `make check` and `make dbt-build` run `make test-role` first, so through make this needs the password to change mid-run; a `pytest` or `dbt` started by hand does not refresh the role first. dbt started by hand then also prints the server's line and writes it to its log file, `dbt/logs/dbt.log` (gitignored, on disk until deleted);
2. both values in `pg_stat_activity`, which holds the text of the statement that hands them to `test-role.sql`'s DO blocks from the moment it starts until the next statement starts, visible to superusers (`mesh`, Dagster's connection) and to members of `pg_read_all_stats` or `pg_monitor` (none today); no log keeps it;
3. both values in Docker's daemon log, which records what `docker compose exec -e` sends only when daemon debug logging is on (it is off here);
4. CI's own fixed values for its throwaway warehouse, visible in `.github/workflows/ci.yml` and CI's logs; the real password is generated and used nowhere else (Casey, issue #55 comment 7018635302, item 4).

The terrain inputs are snapshots: dated folders under `SNAPSHOTS_DIR`, each with a sha256 manifest, never changed once written. One make target writes each, on the host: `make p1812-maps` (the ITU-R P.1812 maps), `make dem-1m` (the USGS 1 m elevation tile x72y429), `make osm-snapshot DATE=2026-11-01` (OpenStreetMap buildings and water), `make canopy` (the canopy height tile) and `make dsm OSM_SNAPSHOT=<id> SCRATCH=<dir>` (the lidar surface, built through the PDAL container). `make register-snapshot ID=<id>` records a finished one in the warehouse; the DSM needs its OpenStreetMap snapshot written and registered first. A write is permanent, so `make -n <target>` prints the command without running it. `make check-p1812` then checks the map files against their pinned sha256 values, runs P.1812 on a flat 10 km path from them and expects 149.48 dB. `pipeline/terrain_snapshots/README.md` describes each command.

`make engine CONFIG=<file>` runs the path-loss engine once, with no network, in the engine image, built from the Dagster Dockerfile, the one place the ITM library is built. It checks the config and the folders first, refuses a checkout with uncommitted changes (the run records the commit it ran), and builds only when every check passes. The container reads the snapshots read-only and writes the run under `RUNS_HOST_DIR`, so the config's `output_dir` must be `/runs` or a folder below it, and it sees the config's folder, so keep each config, with the point layers it names, in a folder of its own; `engine/fixtures/fixture-run-itm.yaml` is a complete example on synthetic data. `engine/README.md` describes the config and what a run writes.

## Data credits

Elevation and lidar: USGS 3DEP (public domain). Canopy height: High Resolution Canopy Height Maps by WRI and Meta, version 2, released under CC BY 4.0 on the AWS Open Data registry and under the DINOv3 licence by its authors; both apply and both are carried into every map that uses it. Buildings and roads: © OpenStreetMap contributors (ODbL). Boundary: US Census TIGER. Meshtastic packet data: a single capture from 2026-10-22 to 2026-10-25 from the public broker and the MeshSTL regional broker under their terms of use, held privately; only aggregated, pseudonymised derivatives are published.

## Layout

| Path | Purpose |
| --- | --- |
| `ingester/` | On-demand MQTT capture command, decoder and reduction to snapshot Parquet (Phase 1) |
| `pipeline/` | Dagster code location: settings, logging, dbt assets, later loaders and features |
| `dbt/` | dbt project: staging, core and analytics models and tests |
| `terrain/` | Pure terrain and propagation functions (Phase 3) |
| `docker/` | Dockerfiles, database init SQL, the bucket bootstrap and the scripts behind the make targets |
| `infra/` | Terraform for the cloud phase (Phase 6) |
| `web/`, `arcgis/` | MapLibre site and ArcGIS Pro deliverable (Phase 5) |
| `docs/` | Plan, style guide, data-quality contract, ADRs, status |

Orchestration is Dagster rather than Airflow; ADR 0001 records the trade-off (asset lineage and a light local footprint over the more common line on job postings).

The Dagster image also carries the Irregular Terrain Model (ITM), the radio propagation model the project tests in Phase 3, as a shared library at `/usr/local/lib/libitm.so`. It is compiled in a build stage of `docker/dagster/Dockerfile` from the reference C++ sources published by NTIA (the National Telecommunications and Information Administration), which are public domain; NTIA asks to be acknowledged and for modifications to be recorded, and the two build-time edits are listed in that Dockerfile and in `docs/adr/0002-itm-spike/README.md`. `terrain/itm.py` (Phase 3) loads the library through `ctypes` from the path in the `ITM_LIBRARY_PATH` setting, and `make check-itm` proves the library in the built image still gives the figure the spike measured, then runs `tests/test_itm.py` and `tests/test_path_loss.py` inside the image. Their tests that call the library skip on any machine without it; in the image a skip fails the check. ADR 0002 records why the library was chosen over SPLAT.

## Running on Windows

Install Git for Windows, Docker Desktop with the WSL 2 backend, GNU Make (`choco install make`) and uv (`winget install astral-sh.uv`). Run every command from Git Bash, not PowerShell: the Makefile forces `SHELL := bash` and the recipes use POSIX syntax. Use `uv run <tool>`, never `.venv/Scripts/<tool>` directly, so the lock file is respected.

Line endings: `.gitattributes` forces LF on checkout for every text file, which matters because the init SQL, the bucket bootstrap and the Dockerfiles are read inside Linux containers. If a script fails with `\r: command not found`, the file was saved with CRLF; `git add --renormalize .` fixes it.

Docker Desktop shares `C:\Users` by default. If the repository lives elsewhere, add its drive under Settings, Resources, File sharing, or the bootstrap script mount in `docker-compose.yml` will fail.

Host port 5433 is used for the warehouse because 5432 is commonly taken by another local Postgres; change `WAREHOUSE_PORT` in `.env` if 5433 is taken too.

HTTPS interception: antivirus products that scan TLS (Norton Web Shield is one) re-sign every certificate. uv is told to trust the Windows certificate store through `UV_SYSTEM_CERTS`, set in the Makefile. Python itself is not, so `dbt deps` fails with `CERTIFICATE_VERIFY_FAILED` until you export the scanner's root certificate and add two lines to `.env`:

```
# PowerShell, once
$c = Get-ChildItem Cert:\CurrentUser\Root | Where-Object { $_.Subject -like "*Norton*" } | Select-Object -First 1
Export-Certificate -Cert $c -FilePath "$env:USERPROFILE\.certs\norton-root.cer" -Type CERT
# Git Bash, once (uv sync first so the certifi bundle exists)
uv sync
openssl x509 -inform der -in ~/.certs/norton-root.cer -out ~/.certs/norton-root.pem
cat .venv/Lib/site-packages/certifi/cacert.pem ~/.certs/norton-root.pem > ~/.certs/ca-bundle.pem
```

Then in `.env`: `SSL_CERT_FILE=C:/Users/<you>/.certs/ca-bundle.pem` and `REQUESTS_CA_BUNDLE=C:/Users/<you>/.certs/ca-bundle.pem`. The stack's running containers are unaffected. For the image builds, also set `CA_BUNDLE_PATH=C:/Users/<you>/.certs/ca-bundle.pem`. The make targets then pass the bundle to the builds as a BuildKit secret. It is mounted only for the steps that download over HTTPS (uv, `dbt deps`, the ITM `curl`, the warehouse's apt and `git clone`) and is never stored in an image. A bare `docker compose build` does not pass it; run `make up` instead.

The one container that reaches the internet while it runs is the PDAL one, which streams USGS lidar. With `CA_BUNDLE_PATH` set, `make pdal` mounts the bundle read-only into it and points PDAL's HTTP client (`CURL_CA_INFO`) and GDAL's (`GDAL_CURL_CA_BUNDLE`) at it. Without the bundle the lidar read fails on the certificate after about four minutes of retries.
