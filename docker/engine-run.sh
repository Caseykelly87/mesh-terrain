#!/usr/bin/env bash
# Runs one engine config in the Dagster image; `make engine` is the only caller.
# Usage: ENGINE_COMPOSE='docker compose ...' engine-run.sh <config.yaml> <runs folder>
#
# The config's folder is mounted read-only at /config, so the point layers it
# names must sit in that folder or below it. The runs folder is mounted
# read-write at /runs, and the config's output_dir must be /runs or a folder
# under it: anything else would be written inside the throwaway container and
# lost when it exits. The runs folder may not hold the checkout (its .env
# carries the HMAC key) or a snapshots folder, or lie inside one, since a run
# never writes into a snapshot (snp_01). Every refusal exits 2 with a line
# that starts "make engine: refused", as the engine's own refusals exit 2.
set -euo pipefail

config=$1
runs=$2

refuse() {
    echo "make engine: refused: $*" >&2
    exit 2
}

# Git Bash answers pwd -W with the C:/ form Docker Desktop mounts; elsewhere
# the physical path.
on_windows=false
if pwd -W > /dev/null 2>&1; then on_windows=true; fi

resolve_dir() {
    [ -n "$1" ] || return 0
    (cd "$1" 2> /dev/null && { pwd -W 2> /dev/null || pwd -P; }) || true
}

fold_case() {
    if $on_windows; then printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; else printf '%s' "$1"; fi
}

# True when path $1 is $2 or lies below it.
is_within() {
    local path parent
    path=$(fold_case "$1")
    parent=$(fold_case "${2%/}")
    [ "$path" = "$parent" ] || [ "${path#"$parent"/}" != "$path" ]
}

[ -f "$config" ] || refuse "CONFIG=<file> must name an engine config file (got '$config')"
[ -n "$runs" ] || refuse "RUNS_HOST_DIR is empty; set it in .env or on the command line"
[ -d "$runs" ] || refuse "RUNS_HOST_DIR $runs is not an existing folder"
# A YAML key at the start of a line; quotes and a trailing comment allowed.
grep -Eq "^output_dir:[[:space:]]*[\"']?/runs(/[^\"'[:space:]]*)?[\"']?[[:space:]]*(#.*)?$" "$config" ||
    refuse "$config: output_dir must be /runs or a folder under it, where RUNS_HOST_DIR is mounted"

runs_dir=$(resolve_dir "$runs")
is_within "$(resolve_dir .)" "$runs_dir" && refuse "RUNS_HOST_DIR $runs_dir holds the checkout and its .env"
for snapshots_variable in SNAPSHOTS_DIR SNAPSHOTS_HOST_DIR; do
    snapshots=$(resolve_dir "${!snapshots_variable:-}")
    [ -n "$snapshots" ] || continue
    is_within "$snapshots" "$runs_dir" && refuse "RUNS_HOST_DIR $runs_dir holds $snapshots_variable ($snapshots)"
    is_within "$runs_dir" "$snapshots" && refuse "RUNS_HOST_DIR $runs_dir is inside $snapshots_variable ($snapshots)"
done

config_dir=$(resolve_dir "$(dirname "$config")")
config_name=$(basename "$config")

# Git Bash rewrites an argument that starts with / into a Windows path before
# docker.exe sees it; the two MSYS variables stop that and do nothing elsewhere.
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'
# ENGINE_COMPOSE is split into words on purpose: it is the command and its -f files.
# The calling user owns what the run writes, so a Linux host can rename and
# hash it; the image runs as root otherwise.
# shellcheck disable=SC2086
exec $ENGINE_COMPOSE run --rm --no-deps -T \
    --user "$(id -u):$(id -g)" \
    --volume "$config_dir:/config:ro" \
    --volume "$runs_dir:/runs" \
    dagster-webserver python -m engine run "/config/$config_name"
