#!/usr/bin/env bash
# Checks one engine run, then builds the engine image and runs the config in
# it; `make engine` is the only caller.
# Usage: ENGINE_COMPOSE='docker compose ...' engine-run.sh <config.yaml> <runs folder>
#
# Every check comes before the build, so a refused run builds nothing and
# retags no image. Every refusal exits 2 with a line that starts
# "make engine: refused", as the engine's own refusals exit 2.
#
# The engine service (docker-compose.yml) mounts SNAPSHOTS_HOST_DIR read-only
# at /snapshots; this script adds the config's folder read-only at /config,
# so the point layers a config names must sit in that folder or below it, and
# the runs folder read-write at /runs. Refused, and why:
# - an output_dir other than /runs or a folder under it, given once as a plain
#   line with no .. segment: anything else is written inside the throwaway
#   container and lost when it exits.
# - a runs folder that is, holds or lies inside the checkout, or the main
#   checkout when this is a worktree: a run must not write into the code or
#   beside the .env that carries the HMAC key.
# - a runs folder that holds or lies inside a snapshots folder: a run never
#   writes into a snapshot (snp_01).
# - a config folder that is or holds either checkout, or holds a file named
#   .env or meshstl.env anywhere below it: the folder is mounted whole, and
#   those files carry the HMAC key and the MeshSTL broker credentials.
# - a checkout with uncommitted changes, or one git cannot read: the image is
#   built from the working tree and records HEAD (Casey, PR #43, 1 (a)).
set -euo pipefail

config=$1
runs=$2

refuse() {
    echo "make engine: refused: $*" >&2
    exit 2
}

# Git Bash answers pwd -W with the C:/ form Docker Desktop mounts; elsewhere
# the physical path, so a symlink cannot hide the checkout or a snapshots folder.
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

# True when either path is the other or lies below it.
overlaps() {
    is_within "$1" "$2" || is_within "$2" "$1"
}

[ -f "$config" ] || refuse "CONFIG=<file> must name an engine config file (got '$config')"
[ -n "$runs" ] || refuse "RUNS_HOST_DIR is empty; set it in .env or on the command line"
[ -d "$runs" ] || refuse "RUNS_HOST_DIR $runs is not an existing folder"
[ -n "${SNAPSHOTS_HOST_DIR:-}" ] || refuse "SNAPSHOTS_HOST_DIR is empty; set it in .env or on the command line"
# Docker would create a missing bind-mount source as an empty folder.
[ -d "$SNAPSHOTS_HOST_DIR" ] || refuse "SNAPSHOTS_HOST_DIR $SNAPSHOTS_HOST_DIR is not an existing folder"

# Outside a git checkout (an exported tarball) the current folder stands in
# for both, since make runs from the project folder.
checkout=$(resolve_dir "$(git rev-parse --show-toplevel 2> /dev/null || pwd)")
common_dir=$(git rev-parse --path-format=absolute --git-common-dir 2> /dev/null || echo "$checkout/.git")
main_checkout=$(resolve_dir "$(dirname "$common_dir")")

runs_dir=$(resolve_dir "$runs")
config_dir=$(resolve_dir "$(dirname "$config")")
for root in "$checkout" "$main_checkout"; do
    overlaps "$runs_dir" "$root" && refuse "RUNS_HOST_DIR $runs_dir is, holds or lies inside the checkout $root"
    is_within "$root" "$config_dir" &&
        refuse "the config folder $config_dir is or holds the checkout $root, whose .env carries the HMAC key"
done
for snapshots_variable in SNAPSHOTS_DIR SNAPSHOTS_HOST_DIR; do
    snapshots=$(resolve_dir "${!snapshots_variable:-}")
    [ -n "$snapshots" ] || continue
    is_within "$snapshots" "$runs_dir" && refuse "RUNS_HOST_DIR $runs_dir holds $snapshots_variable ($snapshots)"
    is_within "$runs_dir" "$snapshots" && refuse "RUNS_HOST_DIR $runs_dir is inside $snapshots_variable ($snapshots)"
done

# A bash glob, not find: from PowerShell or cmd, Windows' own find.exe can
# come first on PATH and would fail quietly here.
shopt -s globstar dotglob nullglob
for secret in "$(dirname "$config")"/**/.env "$(dirname "$config")"/**/meshstl.env; do
    refuse "the config folder $config_dir holds $secret; keep a config in a folder of its own"
done
shopt -u globstar dotglob nullglob

# A YAML mapping takes the last of two equal keys, so a second output_dir,
# quoted or not, would move the run; only one plain line passes.
output_lines=$(grep -cE "^[\"']?output_dir[\"']?[[:space:]]*:" "$config" || true)
[ "$output_lines" = 1 ] ||
    refuse "$config: give output_dir once, as a plain line such as 'output_dir: /runs' (found $output_lines)"
# YAML keeps a # with no space before it in the value, and a double-quoted
# value turns \x2e into a dot, so the value may hold neither # nor \, and a
# comment needs a space before it.
grep -Eq "^output_dir:[[:space:]]*[\"']?/runs(/[^\"'[:space:]#\\]*)?[\"']?([[:space:]]+#.*)?[[:space:]]*$" "$config" ||
    refuse "$config: output_dir must be /runs or a folder under it, where RUNS_HOST_DIR is mounted"
output_dir=$(sed -nE "s/^output_dir:[[:space:]]*[\"']?([^\"'[:space:]#\\]*).*/\1/p" "$config")
case "/$output_dir/" in
    */../*) refuse "$config: output_dir $output_dir has a .. segment, which leaves /runs" ;;
esac

# The image is built from the working tree but records HEAD as the engine's
# commit, so a run from a tree with uncommitted changes would name code it did
# not run (Casey, PR #43, 1 (a)). Untracked files count, as engine_git_dirty
# counts them; ignored files such as .env do not. Last, so every other
# refusal still shows in a tree that is being edited.
changes=$(git status --porcelain --untracked-files=normal 2> /dev/null) ||
    refuse "cannot tell whether the checkout has uncommitted changes: git status failed (not a git checkout?)"
[ -z "$changes" ] ||
    refuse "the checkout has uncommitted changes, and the run would record HEAD for code it did not run; commit or stash them first:
$changes"

config_name=$(basename "$config")

# Git Bash rewrites an argument that starts with / into a Windows path before
# docker.exe sees it; the two MSYS variables stop that and do nothing elsewhere.
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'
# ENGINE_COMPOSE is split into words on purpose: it is the command and its -f
# files. The build comes after every check and before the run, so the run uses
# the checkout's code and records its commit (ENGINE_GIT_COMMIT, Makefile).
# shellcheck disable=SC2086
$ENGINE_COMPOSE --profile engine build engine
# The calling user owns what the run writes, so a Linux host can rename and
# hash it; the image runs as root otherwise.
# shellcheck disable=SC2086
exec $ENGINE_COMPOSE --profile engine run --rm --no-deps -T \
    --user "$(id -u):$(id -g)" \
    --volume "$config_dir:/config:ro" \
    --volume "$runs_dir:/runs" \
    engine python -m engine run "/config/$config_name"
