#!/usr/bin/env bash
# Runs one PDAL pipeline in the pdal service; `make pdal` is the only caller.
# Usage: PDAL_COMPOSE='docker compose ...' pdal-run.sh <pipeline.json> <workdir> <stream> <metadata>
#
# The work folder is mounted read-write, so it is checked before anything
# runs: it may not hold the checkout (whose .env carries the HMAC key) or the
# snapshots folder, and inside the snapshots folder it may only be a
# <id>.partial folder directly below it, so no run writes into a finished
# snapshot (ing_02, snp_01). Every refusal exits 2 with a line that starts
# "make pdal: refused".
set -euo pipefail

pipeline=$1
workdir=$2
stream=$3
metadata=$4

refuse() {
    echo "make pdal: refused: $*" >&2
    exit 2
}

# Git Bash answers pwd -W with the C:/ form Docker Desktop mounts; elsewhere
# the physical path, so a symlink cannot hide the snapshots folder.
on_windows=false
if pwd -W > /dev/null 2>&1; then on_windows=true; fi

# Prints nothing for an empty or missing folder; cd "" would stay put and
# answer with the checkout.
resolve_dir() {
    [ -n "$1" ] || return 0
    (cd "$1" 2> /dev/null && { pwd -W 2> /dev/null || pwd -P; }) || true
}

# Windows paths compare without case, as the file system does.
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

[ -f "$pipeline" ] || refuse "PIPELINE=<file> must name a pipeline JSON file (got '$pipeline')"
case "$pipeline" in *.json) ;; *) refuse "PIPELINE must end in .json (got '$pipeline')" ;; esac
[ -d "$workdir" ] || refuse "WORKDIR=<dir> must name an existing folder (got '$workdir')"
case "$stream" in
    "") stream_flag=--stream ;;
    no) stream_flag=--nostream ;;
    *) refuse "STREAM takes only 'no' (got '$stream')" ;;
esac
case "$metadata" in
    /* | *..*) refuse "METADATA is a file name under WORKDIR without '..' (got '$metadata')" ;;
esac

work=$(resolve_dir "$workdir")
checkout=$(resolve_dir .)
is_within "$checkout" "$work" && refuse "WORKDIR $work holds the checkout and its .env"

for snapshots_variable in SNAPSHOTS_DIR SNAPSHOTS_HOST_DIR; do
    snapshots=$(resolve_dir "${!snapshots_variable:-}")
    [ -n "$snapshots" ] || continue
    is_within "$snapshots" "$work" && refuse "WORKDIR $work holds $snapshots_variable ($snapshots)"
    if is_within "$work" "$snapshots"; then
        parent=$(resolve_dir "$work/..")
        case "$work" in *.partial) partial=true ;; *) partial=false ;; esac
        if ! $partial || [ "$(fold_case "$parent")" != "$(fold_case "${snapshots%/}")" ]; then
            refuse "WORKDIR $work is inside $snapshots_variable; only a <id>.partial folder directly below it is allowed"
        fi
    fi
done

pipeline_name=$(basename "$pipeline")
pipeline_path="$(resolve_dir "$(dirname "$pipeline")")/$pipeline_name"
metadata_args=()
if [ -n "$metadata" ]; then metadata_args=(--metadata "/work/$metadata"); fi

# Git Bash rewrites an argument that starts with / into a Windows path before
# docker.exe sees it; the two MSYS variables stop that and do nothing elsewhere.
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'
# PDAL_COMPOSE is split into words on purpose: it is the command and its -f files.
# shellcheck disable=SC2086
exec $PDAL_COMPOSE run --rm --no-deps -T \
    --user "$(id -u):$(id -g)" \
    --volume "$pipeline_path:/pipelines/$pipeline_name:ro" \
    --volume "$work:/work" \
    pdal pdal pipeline "$stream_flag" "${metadata_args[@]}" "/pipelines/$pipeline_name"
