# Run from Git Bash on Windows or any POSIX shell. SHELL is forced to bash so
# that the same recipes work when make is invoked from PowerShell or cmd.
SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

# .env is exported into every recipe, except NODE_ID_HMAC_KEY, so dbt and pytest
# on the host see the same variables Compose uses. The file is created from
# .env.example by `make up`.
#
# The test role's name and password are the exception: a worktree gets them
# from the shell, which exports only those two lines of the main checkout's
# .env (README, Test role), and never a copy of that file (Casey, PR #74). A
# value from the shell wins over the included .env, so a worktree .env copied
# from an older .env.example cannot replace it; the main checkout's .env still
# supplies them when the shell does not.
TEST_ROLE_VARIABLES := WAREHOUSE_TEST_USER WAREHOUSE_TEST_PASSWORD
$(foreach name,$(TEST_ROLE_VARIABLES),$(if $(filter environment,$(origin $(name))), \
    $(eval FROM_SHELL_$(name) := $$($(name)))))
-include .env
$(foreach name,$(TEST_ROLE_VARIABLES),$(if $(filter file,$(origin FROM_SHELL_$(name))), \
    $(eval $(name) := $$(FROM_SHELL_$(name)))))
export
unexport TEST_ROLE_VARIABLES $(foreach name,$(TEST_ROLE_VARIABLES),FROM_SHELL_$(name))
# The HMAC key is the one secret no recipe needs from the environment: the
# commands that pseudonymise read it from .env through their settings, and the
# tests must never see it (Casey, 2026-11-13).
unexport NODE_ID_HMAC_KEY

# Some Windows hosts run antivirus or a proxy that re-signs HTTPS traffic; uv's
# bundled root store then rejects PyPI with "invalid peer certificate". The
# system store carries the interceptor's root, and on Linux and macOS the system
# store is the same set of roots, so this is safe everywhere.
export UV_SYSTEM_CERTS := true

# The CA bundle reaches the image build only as a BuildKit secret, and only
# when .env names it (docker/compose.ca-bundle.yml says why it is a second file).
COMPOSE := docker compose$(if $(CA_BUNDLE_PATH), -f docker-compose.yml -f docker/compose.ca-bundle.yml)

# Every image build records the checkout's commit for the engine's run
# manifest (docker-compose.yml, x-dagster-build). Empty outside a checkout,
# and then the engine in that image refuses to run.
export ENGINE_GIT_COMMIT := $(shell git rev-parse HEAD 2> /dev/null)
UV := uv run --frozen
DBT := $(UV) dbt
DBT_ARGS := --project-dir dbt --profiles-dir dbt

# make check and make dbt-build build on a development target, one per track,
# so a Track A run and a Track B run at once never share schemas (Casey, issue
# #55 comment 6966197127, item 3): dev_wh, the default, for Track B, and dev_ta
# for Track A (make check DBT_DEV_TARGET=dev_ta). Any other value, prod
# included, stops make before any recipe runs, since the real build is
# make dbt-build-real alone. The guard is unexported because the bare export
# would expand it, and so raise the error, for every recipe's environment; the
# allowlist and the refusal text are unexported because no recipe reads them.
DBT_DEV_TARGET ?= dev_wh
DBT_DEV_TARGETS := dev_wh dev_ta
DBT_DEV_TARGET_REFUSAL = make check and make dbt-build take DBT_DEV_TARGET=dev_wh or \
    dev_ta, not '$(DBT_DEV_TARGET)'; the real schemas are built only by make \
    dbt-build-real, the lead's target
DBT_DEV_TARGET_GUARD = $(if $(filter-out 1,$(words $(DBT_DEV_TARGET)))$(filter-out \
    $(DBT_DEV_TARGETS),$(DBT_DEV_TARGET)),$(error $(DBT_DEV_TARGET_REFUSAL)))
unexport DBT_DEV_TARGET_GUARD DBT_DEV_TARGETS DBT_DEV_TARGET_REFUSAL
$(if $(filter check dbt-build,$(MAKECMDGOALS)),$(DBT_DEV_TARGET_GUARD))

# The goals that connect as the test role stop before any recipe runs when
# either value is missing, so make check lints nothing first. make up is not
# one of them: a new checkout starts the stack before anyone sets the values,
# and up creates the role only once both are set. Unexported for the reason
# the guard above is.
TEST_ROLE_MISSING = set $(1): in a worktree, export it from the main checkout's .env \
    (README, Test role); in the main checkout, set it in .env as .env.example shows
TEST_ROLE_GUARD = $(foreach name,$(TEST_ROLE_VARIABLES),$(if $($(name)),, \
    $(error $(call TEST_ROLE_MISSING,$(name)))))
unexport TEST_ROLE_MISSING TEST_ROLE_GUARD
$(if $(filter check dbt-build test-role,$(MAKECMDGOALS)),$(TEST_ROLE_GUARD))

.PHONY: help env up down nuke rotate-owner-password check-credentials check check-itm check-definitions check-pdal check-p1812 pdal engine fmt reproduce load capture dbt-deps dbt-build dbt-build-real dbt-debug lint test test-role
.PHONY: p1812-maps dem-1m osm-snapshot canopy dsm register-snapshot

help:
	@echo "make up         start PostGIS, MinIO and Dagster and wait for health checks"
	@echo "make down       stop the stack and remove its containers; the data volumes are kept"
	@echo "                (up, down and rotate-owner-password are the only start and stop; no"
	@echo "                service restarts on its own)"
	@echo "make rotate-owner-password"
	@echo "                start the warehouse alone and set the owner's password to WAREHOUSE_PASSWORD"
	@echo "                from .env (README, Credentials)"
	@echo "make check-credentials"
	@echo "                refuse an empty owner password or MinIO root credential in .env, or one of"
	@echo "                the .env.example placeholders; make up and rotate-owner-password run it first"
	@echo "make nuke       stop the stack and delete every project volume after a typed confirmation"
	@echo "                (make nuke CONFIRM=nuke skips the prompt; without a terminal it is required)"
	@echo "make test-role  create or update the tests' database role in the running warehouse; it"
	@echo "                cannot write raw, raw_dev_wh or raw_dev_ta (make up and make check run it)"
	@echo "make check [DBT_DEV_TARGET=dev_ta]"
	@echo "                ruff, mypy, pytest, dbt deps and dbt build against the local stack, on a"
	@echo "                development target: dev_wh by default (Track B), schemas suffixed _dev_wh and"
	@echo "                sources in raw_dev_wh; dev_ta for Track A, suffix _dev_ta; prod is refused."
	@echo "                The tests and the build connect as the test role"
	@echo "make check-itm  load the ITM library in the Dagster image, reproduce the ADR 0002 figure"
	@echo "                and run tests/test_itm.py and tests/test_path_loss.py there, failing on a skip"
	@echo "make check-p1812 [FIXTURE=<folder> EXPECT_DB=<dB>]"
	@echo "                P.1812 on a flat 10 km path from the ITU map snapshot in SNAPSHOTS_DIR, whose"
	@echo "                files must match their pins, expecting 149.48 dB; FIXTURE names another map"
	@echo "                folder, unpinned, and EXPECT_DB its figure (both on the command line only)"
	@echo "make check-definitions"
	@echo "                load the Dagster code location in the running webserver container"
	@echo "make fmt        ruff format and autofix"
	@echo "make reproduce  rebuild every asset from the raw zone (stub until Phase 1)"
	@echo "make load SNAPSHOT=<id>"
	@echo "                load one finished snapshot from SNAPSHOTS_DIR into the raw schema"
	@echo "make capture HOST=... SOURCE=... TOPICS='...' END_AT=... SNAPSHOT=<id>"
	@echo "                one on-demand MQTT capture run (ADR 0005); never scheduled"
	@echo "make pdal PIPELINE=<file> WORKDIR=<dir> [STREAM=no] [METADATA=<file>]"
	@echo "                one PDAL pipeline in the pinned PDAL image: the file is read-only at"
	@echo "                /pipelines/<file>, WORKDIR is read-write at /work (write outputs there);"
	@echo "                inside SNAPSHOTS_DIR only <id>.partial; --stream unless STREAM=no"
	@echo "make check-pdal print the PDAL version and fail without readers.ept or writers.gdal"
	@echo "make engine CONFIG=<file>"
	@echo "                one engine run in the engine image (built from the Dagster Dockerfile, no network):"
	@echo "                SNAPSHOTS_HOST_DIR read-only at /snapshots, RUNS_HOST_DIR read-write at /runs,"
	@echo "                the config's folder read-only at /config; output_dir must be /runs or below;"
	@echo "                checks run before the build and refuse uncommitted changes; CONFIG counts"
	@echo "                only on the make command line; never scheduled"
	@echo ""
	@echo "Terrain snapshots: each target writes one snapshot under SNAPSHOTS_DIR (ROOT=<dir> instead)"
	@echo "and refuses an id that exists. A write is permanent; run one only after Casey's yes."
	@echo "DATE, OSM_SNAPSHOT, SCRATCH, ID and ROOT count only on the make command line."
	@echo "make p1812-maps the ITU-R P.1812 maps, itu-r-p1812-8-202509"
	@echo "make dem-1m     the USGS 1 m DEM tile, 3dep-1m-mo-saintlouis-2017-x72y429"
	@echo "make osm-snapshot DATE=YYYY-MM-01"
	@echo "                Geofabrik's Missouri and Illinois extracts of that date, osm-geofabrik-mo-il-<DATE>"
	@echo "make canopy     the CHMv2 canopy tile, canopy-v2-0231113110"
	@echo "make dsm OSM_SNAPSHOT=<id> SCRATCH=<dir>"
	@echo "                the lidar DSM, ept-mo-stlouis-2017-x72y429, through make pdal; needs the OSM"
	@echo "                snapshot written and registered; window rasters go to SCRATCH, outside the snapshots"
	@echo ""
	@echo "Registering a snapshot: the one target here that writes to the warehouse."
	@echo "make register-snapshot ID=<id>"
	@echo "                check a finished snapshot against its manifest and record it in raw.snapshot"
	@echo ""
	@echo "The lead's target, which no agent runs:"
	@echo "make dbt-build-real"
	@echo "                dbt build on the target prod: reads the real raw schema and builds the real"
	@echo "                staging, core and analytics schemas"

env:
	@test -f .env || cp .env.example .env

# The owner password and the MinIO root credentials in .env.example are public,
# so neither make up nor make rotate-owner-password starts anything while .env
# holds an empty value or one of those placeholders. The script prints only the
# name of a variable that fails; it runs with BASH_ENV and SHELLOPTS unset so
# no inherited tracing prints a value as it is compared.
check-credentials: env
	env -u BASH_ENV -u SHELLOPTS bash docker/check-credentials.sh

# Health waiting is a script rather than compose --wait; the script says why.
# The test role follows only when both values are set; the note names the
# variables, never their values. The sub-make names this Makefile so it also
# works when make was started with -f from another folder.
up: check-credentials
	$(COMPOSE) up --build --detach
	bash docker/wait-for-stack.sh
	@if [ -n "$${WAREHOUSE_TEST_USER:-}" ] && [ -n "$${WAREHOUSE_TEST_PASSWORD:-}" ]; then \
	    $(MAKE) --no-print-directory -f '$(firstword $(MAKEFILE_LIST))' test-role; \
	else \
	    echo "make up: the test role was not created: WAREHOUSE_TEST_USER or"; \
	    echo "WAREHOUSE_TEST_PASSWORD is not set; make check creates it once both are (README, Test role)"; \
	fi

# The database role the tests and the development dbt builds connect as
# (docker/warehouse/test-role.sql says what it may and may not do). It is
# applied here, on every make check and make dbt-build, and on make up once
# both values are set, and not from initdb/, which
# runs only on a fresh volume, so an existing warehouse gets it too. The bare
# -e names pass the name and password from make's environment into the
# container, where psql reads them, so the password is on no command line.
# pg_isready waits on TCP because the server listens there only once the init
# scripts of a fresh volume are done; until then the health check can pass
# against the temporary server that runs them.
TEST_ROLE_PSQL = tries=0; until pg_isready -q -h 127.0.0.1; do \
    tries=$$((tries + 1)); [ $$tries -lt 60 ] || exit 1; sleep 1; done; \
    psql -X -q -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -f -
TEST_ROLE_REQUIRED = : "$${WAREHOUSE_TEST_USER:?set WAREHOUSE_TEST_USER, see README, Test role}" \
    "$${WAREHOUSE_TEST_PASSWORD:?set WAREHOUSE_TEST_PASSWORD, see README, Test role}"
# The development dbt targets run as the test role: dbt replaces a view or
# table only as its owner, and the fixture build in the tests builds into the
# same schemas as that role. The shell reads the values from make's
# environment, so the recipe text holds only their names.
DBT_AS_TEST_ROLE = WAREHOUSE_USER="$${WAREHOUSE_TEST_USER:?set WAREHOUSE_TEST_USER, see README, Test role}" \
    WAREHOUSE_PASSWORD="$${WAREHOUSE_TEST_PASSWORD:?set WAREHOUSE_TEST_PASSWORD, see README, Test role}"
unexport TEST_ROLE_PSQL TEST_ROLE_REQUIRED DBT_AS_TEST_ROLE

test-role: env
	@$(TEST_ROLE_REQUIRED)
	$(COMPOSE) exec -T -e WAREHOUSE_TEST_USER -e WAREHOUSE_TEST_PASSWORD warehouse \
	    sh -c '$(TEST_ROLE_PSQL)' < docker/warehouse/test-role.sql

# Postgres reads POSTGRES_PASSWORD only when it creates a fresh volume, so a
# new WAREHOUSE_PASSWORD in .env reaches an existing warehouse only through
# this target (README, Credentials). It starts the warehouse alone, so Dagster
# does not try the new password first, waits until the server listens on TCP
# (only the final server does, after any init scripts), and runs
# docker/warehouse/rotate-owner-password.sql through psql over the
# container's local socket, which the image trusts without a password, as the
# healthcheck's own login does; so the old password is never needed and the
# owner cannot be locked out. The bare -e name passes the new value from .env
# into the container, where psql reads it, so it is on no command line. The
# wait allows 150 s, about what the warehouse health check allows a fresh
# volume (start period plus retries), and says why it stops.
ROTATE_PSQL = tries=0; until pg_isready -q -h 127.0.0.1; do \
    tries=$$((tries + 1)); [ $$tries -lt 150 ] || { \
    echo "make rotate-owner-password: the warehouse did not listen on TCP within 150 s" >&2; \
    exit 1; }; sleep 1; done; \
    psql -X -q -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -f -
unexport ROTATE_PSQL

rotate-owner-password: check-credentials
	$(COMPOSE) up --detach warehouse
	$(COMPOSE) exec -T -e WAREHOUSE_PASSWORD warehouse sh -c '$(ROTATE_PSQL)' < docker/warehouse/rotate-owner-password.sql

# Keeps the named volumes so stopping never loses data; make nuke deletes them.
down:
	$(COMPOSE) down --remove-orphans

# CONFIRM counts only when typed on the make command line. The bare export
# above would also pass one from the shell environment or .env, so the word
# goes to the script as an argument and only when origin says command line.
NUKE_CONFIRM := $(if $(filter command line,$(origin CONFIRM)),$(CONFIRM))

# The script lists every project volume and asks before deleting anything.
nuke: env
	bash docker/nuke.sh '$(NUKE_CONFIRM)'

lint:
	$(UV) ruff check .
	$(UV) ruff format --check .
	$(UV) mypy

test:
	$(UV) pytest --cov --cov-report=term-missing

dbt-deps:
	$(DBT) deps $(DBT_ARGS)

# As the owner, not the test role: dbt debug prints the connection's user, and
# the test role's values never appear in a transcript or log (Casey, issue #55
# comment 7017280290, item 2). It only connects and reads.
dbt-debug: dbt-deps
	$(DBT) debug $(DBT_ARGS)

# A development target builds into staging_<target>, core_<target> and
# analytics_<target> and reads raw_<target>, never the real schemas
# (dbt/macros/target_schemas.sql). dev_wh is also the profile's default;
# naming the target here keeps make check off the real schemas if the default
# ever changes. The guard repeats the check above for a run that reaches this
# recipe from another goal. test-role runs first so dbt never logs in with a
# stale password: the server's refusal would carry the role's name into dbt's
# output and the server log.
dbt-build: dbt-deps test-role
	$(DBT_DEV_TARGET_GUARD)$(DBT_AS_TEST_ROLE) $(DBT) build $(DBT_ARGS) --target $(DBT_DEV_TARGET)

# The real raw, staging, core and analytics schemas. The lead's target only
# (dbt/README.md); no agent's unit runs it. dbt refuses a run on prod without
# the real_build variable, so DBT_TARGET=prod alone cannot start one; this is
# the one recipe that passes it. It connects as the owner role, WAREHOUSE_USER,
# since the test role cannot write the real schemas.
dbt-build-real: dbt-deps
	$(DBT) build $(DBT_ARGS) --target prod --vars '{real_build: true}'

# test-role runs before the tests so the grants they check are the file's,
# whatever ran against the warehouse since the last make up.
check: env lint test-role test dbt-build

# Runs inside the Dagster image, not on the host, because the library is built
# for Linux there (ADR 0002). The build comes first because compose run reuses
# whatever image exists, so an edit to the ITM stage would otherwise test the old
# library; with nothing changed it is a cache hit. The first run proves the .so
# loads and exports the point-to-point entry point; the second feeds the spike
# script on stdin (no bind mount, so MSYS path rewriting cannot break it) and
# matches the measured flat-path loss as a fixed string, so a changed compiler
# or source fails here.
#
# The third run is the library tests of terrain/itm.py and terrain/path_loss.py
# and the engine's itm run, which skip on any host without libitm.so; in the
# image a skip means the library went missing, so it fails the check. Only that
# one engine test runs here: the others read docs/ and need a git checkout,
# which the image lacks, and they run on the host in the tests job. The image
# has no dev group and no tests folder: the throwaway container installs the
# dev group from uv.lock, hash-checked, which needs PyPI, and gets tests/ as a
# read-only mount. The two MSYS variables stop Git Bash rewriting the
# container paths and do nothing elsewhere. With CA_BUNDLE_PATH set the bundle
# is mounted for uv, as the build secret is at build time.
ITM_TESTS := tests/test_itm.py tests/test_path_loss.py \
    tests/test_engine_run.py::test_the_itm_fixture_runs_in_the_image
ITM_TESTS_IN_IMAGE := uv sync --frozen --inexact --no-install-project --group dev --quiet \
    && { python -m pytest -p no:cacheprovider -rs $(ITM_TESTS) > /tmp/pytest.log; status=$$?; } \
    && cat /tmp/pytest.log \
    && [ $$status -eq 0 ] \
    && { ! grep -q skipped /tmp/pytest.log || { echo "check-itm: a libitm test skipped"; exit 1; }; }

check-itm: env
	$(COMPOSE) build dagster-webserver
	$(COMPOSE) run --rm --no-deps -T dagster-webserver python -c "import ctypes; print(ctypes.CDLL('/usr/local/lib/libitm.so').ITM_P2P_CR_Ex)"
	output=$$($(COMPOSE) run --rm --no-deps -T dagster-webserver python - < docs/adr/0002-itm-spike/check_itm.py); \
	    echo "$$output"; \
	    echo "$$output" | grep -qF "itm basic transmission loss: 151.76 dB"
	MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' $(COMPOSE) run --rm --no-deps -T \
	    --volume "$$(pwd -W 2> /dev/null || pwd -P)/tests:/opt/mesh-terrain/tests:ro" \
	    $(if $(CA_BUNDLE_PATH),--volume '$(CA_BUNDLE_PATH):/run/secrets/ca_bundle:ro' --env SSL_CERT_FILE=/run/secrets/ca_bundle) \
	    dagster-webserver sh -c '$(ITM_TESTS_IN_IMAGE)'

# The webserver stays healthy when the code location fails to import: it serves
# the error in the UI and /server_info still answers. This loads the workspace
# the webserver uses, in its running container, so an import that works on the
# host but not in the image fails here. Needs make up first. The workspace path
# is expanded inside the container: Git Bash rewrites an argument that starts
# with / into a Windows path before docker.exe sees it.
check-definitions:
	$(COMPOSE) exec -T dagster-webserver sh -c 'dagster definitions validate -w "$$DAGSTER_HOME/workspace.yaml"'

# One PDAL pipeline in the pinned PDAL image (the pdal service). Example:
# make pdal PIPELINE=terrain/pipelines/dsm.json WORKDIR=C:/data/snapshots/<id>.partial
# Only the pipeline file is mounted, read-only at /pipelines/<file>; WORKDIR is
# read-write at /work, the working directory, so a pipeline writes under /work
# and sees nothing else of the host. --stream is the default so that a
# pipeline that cannot stream fails instead of holding a lidar tile in memory;
# STREAM=no runs it in standard mode. METADATA=<file> writes PDAL's metadata
# to that path under WORKDIR; when streaming it holds each stage's SRS and the
# output file names but no point counts. docker/pdal-run.sh refuses a WORKDIR
# that holds the checkout or the snapshots folder, or that lies inside the
# snapshots folder and is not a <id>.partial folder directly below it (ing_02).
pdal: env
	PDAL_COMPOSE='$(COMPOSE)' bash docker/pdal-run.sh '$(PIPELINE)' '$(WORKDIR)' '$(STREAM)' '$(METADATA)'

# One engine run (docs/INTERFACE.md) in the engine service, built from the
# Dagster Dockerfile, the one place libitm.so exists. Example:
# make engine CONFIG=engine/fixtures/fixture-run-itm.yaml
# docker/engine-run.sh checks the config, RUNS_HOST_DIR and SNAPSHOTS_HOST_DIR,
# and only then builds the image, so a refused run builds nothing, and a run
# uses the checkout's code and records its commit (ENGINE_GIT_COMMIT above).
# The script says what it mounts and refuses. Never scheduled.
engine: env
	$(call require,CONFIG,<engine config file>)ENGINE_COMPOSE='$(COMPOSE)' bash docker/engine-run.sh '$(call typed,CONFIG)' '$(RUNS_HOST_DIR)'

# The DSM pipeline needs the EPT reader and the GDAL writer; this fails when
# the pinned image lacks either, and prints the version a manifest records.
check-pdal: env
	$(COMPOSE) run --rm --no-deps -T pdal sh -c 'pdal --version && drivers=$$(pdal --drivers) && for driver in readers.ept writers.gdal; do echo "$$drivers" | grep -q "^$$driver " || { echo "missing $$driver"; exit 1; }; done && echo "readers.ept and writers.gdal present"'

# One on-demand capture run (ADR 0005). Example:
# make capture HOST=mqtt.meshtastic.org SOURCE=public TOPICS='msh/US/MeshSTL/# msh/US/MO/#' \
#   END_AT=2026-12-01T00:00:00+00:00 SNAPSHOT=meshtastic-capture-2026-11-30
# The script reads the key from .env through its settings and refuses the
# MeshSTL broker without Casey's approval date, which this target does not pass.
capture:
	$(UV) python -I docs/adr/0003-spike/capture.py --host $(HOST) --source $(SOURCE) \
	    $(foreach topic,$(TOPICS),--topic '$(topic)') --end-at $(END_AT) \
	    --max-raw-bytes $(or $(MAX_RAW_BYTES),10000000000) --snapshot-id $(SNAPSHOT)

# Load one finished snapshot into raw; refuses a .partial folder or a manifest mismatch.
load:
	$(UV) python -m ingester.load $(SNAPSHOT)

# A variable's value only when it was typed on the make command line, as
# NUKE_CONFIRM reads CONFIRM: the bare export above would otherwise let a stray
# variable in the shell or .env start or redirect a snapshot write.
# Usage: $(call typed,NAME).
typed = $(if $(filter command line,$(origin $(1))),$($(1)))

# Stops make before anything runs when a variable was not typed on the command
# line, so make -n shows the refusal as well. Usage: $(call require,NAME,<what
# it holds>). Unexported because the bare export would expand it, and so raise
# the error, for every recipe's environment.
require = $(if $(call typed,$(1)),,$(error make $@ needs $(1)=$(2) on the command line))
unexport require

# The terrain snapshot commands (pipeline/terrain_snapshots/README.md). They
# run on the host, like load, because the DSM command calls make pdal from the
# checkout and Compose mounts the snapshots folder read-only. Behind Norton the
# downloads need SSL_CERT_FILE and the DSM's lidar reads need CA_BUNDLE_PATH,
# both from .env. Each writes a snapshot, which is permanent, so none runs
# without Casey's yes on its checklist; make -n prints the command instead.
TERRAIN_SNAPSHOT := $(UV) python -m pipeline.terrain_snapshots
SNAPSHOT_ROOT = $(if $(call typed,ROOT),--root '$(call typed,ROOT)')

p1812-maps:
	$(TERRAIN_SNAPSHOT) itu-maps $(SNAPSHOT_ROOT)

dem-1m:
	$(TERRAIN_SNAPSHOT) dem $(SNAPSHOT_ROOT)

osm-snapshot:
	$(call require,DATE,YYYY-MM-01)$(TERRAIN_SNAPSHOT) osm --date '$(call typed,DATE)' \
	    $(SNAPSHOT_ROOT)

canopy:
	$(TERRAIN_SNAPSHOT) canopy $(SNAPSHOT_ROOT)

dsm:
	$(call require,OSM_SNAPSHOT,<id>)$(call require,SCRATCH,<dir>)$(TERRAIN_SNAPSHOT) dsm \
	    --osm-snapshot '$(call typed,OSM_SNAPSHOT)' --scratch '$(call typed,SCRATCH)' $(SNAPSHOT_ROOT)

register-snapshot:
	$(call require,ID,<snapshot id>)$(TERRAIN_SNAPSHOT) register '$(call typed,ID)' $(SNAPSHOT_ROOT)

# P.1812 on a flat 10 km path (docker/check_p1812.py); it reads the map folder
# and writes nothing. Without FIXTURE it checks the ITU snapshot in
# SNAPSHOTS_DIR, whose files must match the pins in
# pipeline/terrain_snapshots/itu_maps.py, against 149.48 dB. FIXTURE checks
# another map folder, such as tests/fixtures/snapshots/p1812-maps-synthetic,
# skips the pins and needs that folder's figure in EXPECT_DB. EXPECT_DB without
# FIXTURE is refused, and both count only on the command line, so no value
# from the shell or .env can let a stand-in pass as the ITU maps.
P1812_MAPS_ID := itu-r-p1812-8-202509
P1812_FIXTURE = $(call typed,FIXTURE)
P1812_EXPECT_DB = $(call typed,EXPECT_DB)
P1812_GUARD = $(if $(P1812_FIXTURE),$(call require,EXPECT_DB,<dB>), \
    $(if $(P1812_EXPECT_DB),$(error make check-p1812: EXPECT_DB needs FIXTURE=<folder>; \
    the ITU snapshot must give 149.48 dB)) \
    $(if $(SNAPSHOTS_DIR),,$(error make check-p1812 needs SNAPSHOTS_DIR)))
unexport P1812_GUARD

check-p1812:
	$(strip $(P1812_GUARD))$(UV) python -I docker/check_p1812.py \
	    '$(or $(P1812_FIXTURE),$(SNAPSHOTS_DIR)/$(P1812_MAPS_ID))' \
	    $(if $(P1812_FIXTURE),--fixture --expect-db '$(P1812_EXPECT_DB)')

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
