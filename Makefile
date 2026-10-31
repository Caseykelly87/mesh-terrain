# Run from Git Bash on Windows or any POSIX shell. SHELL is forced to bash so
# that the same recipes work when make is invoked from PowerShell or cmd.
SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

# .env is exported into every recipe so dbt and pytest on the host see the same
# variables Compose uses. The file is created from .env.example by `make up`.
-include .env
export

# Some Windows hosts run antivirus or a proxy that re-signs HTTPS traffic; uv's
# bundled root store then rejects PyPI with "invalid peer certificate". The
# system store carries the interceptor's root, and on Linux and macOS the system
# store is the same set of roots, so this is safe everywhere.
export UV_SYSTEM_CERTS := true

COMPOSE := docker compose
UV := uv run --frozen
DBT := $(UV) dbt
DBT_ARGS := --project-dir dbt --profiles-dir dbt

.PHONY: help env up down nuke check check-itm fmt reproduce dbt-deps dbt-build dbt-debug lint test

help:
	@echo "make up         start PostGIS, MinIO and Dagster and wait for health checks"
	@echo "make down       stop the stack and remove its containers; the data volumes are kept"
	@echo "                (up and down are the only start and stop; no service restarts on its own)"
	@echo "make nuke       stop the stack and delete its data volumes after a typed confirmation"
	@echo "                (without a terminal it refuses unless run as make nuke CONFIRM=nuke)"
	@echo "make check      ruff, mypy, pytest, dbt deps and dbt build against the local stack"
	@echo "make check-itm  load the ITM library in the Dagster image and reproduce the ADR 0002 figure"
	@echo "make fmt        ruff format and autofix"
	@echo "make reproduce  rebuild every asset from the raw zone (stub until Phase 1)"

env:
	@test -f .env || cp .env.example .env

# Health waiting is a script rather than compose --wait; the script says why.
up: env
	$(COMPOSE) up --build --detach
	bash docker/wait-for-stack.sh

# Keeps the named volumes so stopping never loses data; make nuke deletes them.
down:
	$(COMPOSE) down --remove-orphans

# CONFIRM reaches the script through the bare export above. The script lists
# the volumes and asks before deleting anything.
nuke: env
	bash docker/nuke.sh

lint:
	$(UV) ruff check .
	$(UV) ruff format --check .
	$(UV) mypy

test:
	$(UV) pytest --cov --cov-report=term-missing

dbt-deps:
	$(DBT) deps $(DBT_ARGS)

dbt-debug: dbt-deps
	$(DBT) debug $(DBT_ARGS)

dbt-build: dbt-deps
	$(DBT) build $(DBT_ARGS)

check: env lint test dbt-build

# Runs inside the Dagster image, not on the host, because the library is built
# for Linux there (ADR 0002). The build comes first because compose run reuses
# whatever image exists, so an edit to the ITM stage would otherwise test the old
# library; with nothing changed it is a cache hit. The first run proves the .so
# loads and exports the point-to-point entry point; the second feeds the spike
# script on stdin (no bind mount, so MSYS path rewriting cannot break it) and
# matches the measured flat-path loss as a fixed string, so a changed compiler
# or source fails here.
check-itm: env
	$(COMPOSE) build dagster-webserver
	$(COMPOSE) run --rm --no-deps -T dagster-webserver python -c "import ctypes; print(ctypes.CDLL('/usr/local/lib/libitm.so').ITM_P2P_CR_Ex)"
	output=$$($(COMPOSE) run --rm --no-deps -T dagster-webserver python - < docs/adr/0002-itm-spike/check_itm.py); \
	    echo "$$output"; \
	    echo "$$output" | grep -qF "itm basic transmission loss: 151.76 dB"

fmt:
	$(UV) ruff check --fix .
	$(UV) ruff format .

# Stub until Phase 1 lands the raw loader. It will then materialise every
# Dagster asset from the raw zone in dependency order through the dbt and
# features layers, equivalent to a full rebuild from Parquet.
reproduce:
	@echo "reproduce: nothing to rebuild yet. From Phase 1 this target runs"
	@echo "  dagster asset materialize --select '*' -m pipeline.definitions"
	@echo "to rebuild every asset from the raw zone."
