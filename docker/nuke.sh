#!/bin/bash
# Stops the stack and deletes this project's Compose volumes: the warehouse
# database, the Dagster run history and the object store contents. `make down`
# keeps them, so this is the one target that throws data away. It lists what it
# will delete and asks for a typed word first. With no terminal to ask on (CI,
# a script, a pipe) it refuses unless CONFIRM carries the word, so nothing
# deletes data by accident.
set -euo pipefail

word=nuke
# Hard-coded rather than read from COMPOSE_PROJECT_NAME, so a stray value in
# the environment cannot point this at another project's volumes. Matches
# `name:` in docker-compose.yml.
project=mesh-terrain

# Compose labels every volume it creates with its project, so the filter never
# selects another project's volumes or anonymous volumes. Volume names cannot
# contain whitespace, so the list splits safely on it.
volumes=$(docker volume ls --quiet --filter "label=com.docker.compose.project=$project")

if [ -z "$volumes" ]; then
    echo "make nuke: no $project volumes exist; nothing to delete."
    exit 0
fi

echo "make nuke: this stops the stack and permanently deletes these volumes:"
for volume in $volumes; do
    echo "  $volume"
done

if [ -n "${CONFIRM:-}" ]; then
    answer=$CONFIRM
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

# The containers go first because Docker refuses to remove a volume a
# container still uses.
docker compose -p "$project" down --remove-orphans
# shellcheck disable=SC2086
docker volume rm $volumes
echo "make nuke: deleted. The next make up starts from empty volumes."
