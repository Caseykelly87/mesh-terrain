#!/bin/bash
# Stops the stack and deletes every Docker volume of the mesh-terrain project:
# today the warehouse database, the Dagster run history and the object store
# contents, and any project volume added later. `make down` keeps them, so this
# is the one target that throws data away. It lists what it will delete and asks
# for a typed word first. With no terminal to ask on (CI, a script, a pipe) it
# refuses unless the word is given as the first argument, which `make nuke`
# passes only from `make nuke CONFIRM=nuke` on the command line, never from the
# environment or .env, so nothing deletes data by accident.
#
# Usage: bash docker/nuke.sh [confirmation-word]
set -euo pipefail

word=nuke
confirm=${1:-}
# Hard-coded rather than read from COMPOSE_PROJECT_NAME, so a stray value in
# the environment cannot point this at another project's volumes. Matches
# `name:` in docker-compose.yml.
project=mesh-terrain

# Compose labels every volume it creates with its project, so the filter
# selects all of this project's volumes, including ones added later, and never
# another project's volumes or anonymous volumes. Volume names cannot contain
# whitespace, so the list splits safely on it.
volumes=$(docker volume ls --quiet --filter "label=com.docker.compose.project=$project")

if [ -z "$volumes" ]; then
    echo "make nuke: no $project volumes exist; nothing to delete."
    exit 0
fi

echo "make nuke: this stops the stack and permanently deletes every $project volume:"
for volume in $volumes; do
    echo "  $volume"
done

if [ -n "$confirm" ]; then
    answer=$confirm
elif [ -t 0 ]; then
    read -r -p "Type $word to delete them, anything else to cancel: " answer || answer=""
else
    echo "make nuke: no terminal to confirm on; nothing deleted. Run 'make nuke CONFIRM=$word' to delete without a prompt." >&2
    exit 1
fi

if [ "$answer" != "$word" ]; then
    echo "make nuke: cancelled; nothing deleted." >&2
    exit 1
fi

docker compose -p "$project" down --remove-orphans

# Docker refuses to remove a volume a container still uses, and volume rm with
# several names removes the free ones before failing on the busy one. Checking
# every volume first stops the delete before any volume goes. The check comes
# after compose down, so only containers outside the stack can trip it.
in_use=""
for volume in $volumes; do
    if [ -n "$(docker ps -aq --filter "volume=$volume")" ]; then
        in_use="$in_use $volume"
    fi
done
if [ -n "$in_use" ]; then
    echo "make nuke: these volumes are still used by a container; nothing deleted:" >&2
    for volume in $in_use; do
        echo "  $volume" >&2
        docker ps -a --filter "volume=$volume" --format '    {{.Names}} ({{.Status}})' >&2
    done
    echo "Remove those containers, then run make nuke again." >&2
    exit 1
fi

# shellcheck disable=SC2086
docker volume rm $volumes
echo "make nuke: deleted. The next make up starts from empty volumes."
