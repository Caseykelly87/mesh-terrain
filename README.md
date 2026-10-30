# mesh-terrain

How much of the variation in Meshtastic LoRa link quality across the St. Louis metro is explained by terrain, and what explains the rest. A streaming-to-batch pipeline ingests the public Meshtastic MQTT feed, models it in PostGIS with dbt and Dagster, derives terrain features from USGS 3DEP elevation data, and tests a standard radio propagation model against observed link quality. The plan, data model and phases are in `docs/PLAN.md`; where the project actually stands is in `docs/STATUS.md`.

## Quick start

Requirements: Docker (Compose v2), GNU Make, [uv](https://docs.astral.sh/uv/) 0.13, Python 3.12 (uv installs it if missing).

```
make up      # builds and starts PostGIS with h3-pg, the object store and Dagster, waits for health checks
make check   # ruff, mypy, pytest, dbt deps and dbt build against the local stack
make down    # stops the stack and removes its volumes
```

`make up` and `make down` are the only way to start and stop the stack. Every Compose service has `restart: "no"`, so opening Docker Desktop or restarting the machine starts nothing; run `make up` again. Do not start or stop the services with `docker compose up`, `docker start` or the Docker Desktop buttons: they skip the health and exit-code checks in `docker/wait-for-stack.sh`.

`make up` copies `.env.example` to `.env` if it is missing. Dagster is at http://localhost:3000, the warehouse at localhost:5433 (database `mesh_terrain`, user `mesh`), the object store API at localhost:9000 and its console at localhost:9001 (user `minio`). `make fmt` formats; `make reproduce` will rebuild every asset from the raw zone once Phase 1 lands the loader.

To run the pre-commit hooks on every commit: `uv run pre-commit install`.

## Layout

| Path | Purpose |
| --- | --- |
| `ingester/` | MQTT subscriber and Parquet landing (Phase 1) |
| `pipeline/` | Dagster code location: settings, logging, dbt assets, later loaders and features |
| `dbt/` | dbt project: staging, core and analytics models and tests |
| `terrain/` | Pure terrain and propagation functions (Phase 3) |
| `docker/` | Dockerfiles, database init SQL and the bucket bootstrap |
| `infra/` | Terraform for the cloud phase (Phase 6) |
| `web/`, `arcgis/` | MapLibre site and ArcGIS Pro deliverable (Phase 5) |
| `docs/` | Plan, style guide, data-quality contract, ADRs, status |

Orchestration is Dagster rather than Airflow; ADR 0001 records the trade-off (asset lineage and a light local footprint over the more common line on job postings).

The Dagster image also carries the Irregular Terrain Model (ITM), the radio propagation model the project tests in Phase 3, as a shared library at `/usr/local/lib/libitm.so`. It is compiled in a build stage of `docker/dagster/Dockerfile` from the reference C++ sources published by NTIA (the National Telecommunications and Information Administration), which are public domain; NTIA asks to be acknowledged and for modifications to be recorded, and the two build-time edits are listed in that Dockerfile and in `docs/adr/0002-itm-spike/README.md`. `terrain/itm.py` (Phase 3) loads the library through `ctypes` from the path in the `ITM_LIBRARY_PATH` setting, and `make check-itm` proves the library in the built image still gives the figure the spike measured. ADR 0002 records why the library was chosen over SPLAT.

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

Then in `.env`: `SSL_CERT_FILE=C:/Users/<you>/.certs/ca-bundle.pem` and `REQUESTS_CA_BUNDLE=C:/Users/<you>/.certs/ca-bundle.pem`. Container builds are unaffected.
