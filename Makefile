# Run from Git Bash on Windows or any POSIX shell. SHELL is forced to bash so
# that the same recipes work when make is invoked from PowerShell or cmd.
SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

# .env is exported into every recipe, except NODE_ID_HMAC_KEY, so dbt and pytest
# on the host see the same variables Compose uses. The file is created from
# .env.example by `make up`.
-include .env
export
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

.PHONY: help env up down nuke check check-itm check-definitions check-pdal check-p1812 pdal engine fmt reproduce load capture dbt-deps dbt-build dbt-build-real dbt-debug lint test
.PHONY: p1812-maps dem-1m osm-snapshot canopy dsm register-snapshot

help:
	@echo "make up         start PostGIS, MinIO and Dagster and wait for health checks"
	@echo "make down       stop the stack and remove its containers; the data volumes are kept"
	@echo "                (up and down are the only start and stop; no service restarts on its own)"
	@echo "make nuke       stop the stack and delete every project volume after a typed confirmation"
	@echo "                (make nuke CONFIRM=nuke skips the prompt; without a terminal it is required)"
	@echo "make check [DBT_DEV_TARGET=dev_ta]"
	@echo "                ruff, mypy, pytest, dbt deps and dbt build against the local stack, on a"
	@echo "                development target: dev_wh by default (Track B), schemas suffixed _dev_wh and"
	@echo "                sources in raw_dev_wh; dev_ta for Track A, suffix _dev_ta; prod is refused"
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

# Health waiting is a script rather than compose --wait; the script says why.
up: env
	$(COMPOSE) up --build --detach
	bash docker/wait-for-stack.sh

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

dbt-debug: dbt-deps
	$(DBT) debug $(DBT_ARGS)

# A development target builds into staging_<target>, core_<target> and
# analytics_<target> and reads raw_<target>, never the real schemas
# (dbt/macros/target_schemas.sql). dev_wh is also the profile's default;
# naming the target here keeps make check off the real schemas if the default
# ever changes. The guard repeats the check above for a run that reaches this
# recipe from another goal.
dbt-build: dbt-deps
	$(DBT_DEV_TARGET_GUARD)$(DBT) build $(DBT_ARGS) --target $(DBT_DEV_TARGET)

# The real raw, staging, core and analytics schemas. The lead's target only
# (dbt/README.md); no agent's unit runs it. dbt refuses a run on prod without
# the real_build variable, so DBT_TARGET=prod alone cannot start one; this is
# the one recipe that passes it.
dbt-build-real: dbt-deps
	$(DBT) build $(DBT_ARGS) --target prod --vars '{real_build: true}'

check: env lint test dbt-build

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
