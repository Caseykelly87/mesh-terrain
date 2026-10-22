#!/bin/bash
# Waits until every long-running service reports healthy, then fails if any
# container is in a state it should not be in: a one-shot that did not exit 0,
# or a long-running service that exited or is restarting. Compose's own --wait
# cannot do this because some releases count a one-shot's clean exit as a
# failure and none of them notice a service that crashed and restarted.
set -euo pipefail

long_running="warehouse minio dagster-webserver dagster-daemon"
one_shot="dagster-migrate minio-bootstrap"
deadline=$((SECONDS + ${WAIT_TIMEOUT_S:-300}))

state_of() {
    docker compose ps -a --format '{{.State}}' "$1"
}

health_of() {
    docker compose ps -a --format '{{.Health}}' "$1"
}

fail() {
    echo "make up: $1" >&2
    docker compose ps -a >&2
    exit 1
}

while true; do
    pending=""
    for service in $long_running; do
        case "$(state_of "$service")" in
            exited | dead | restarting) fail "service ${service} is not running" ;;
        esac
        [ "$(health_of "$service")" = "healthy" ] || pending="${pending} ${service}"
    done
    [ -n "$pending" ] || break
    [ "$SECONDS" -lt "$deadline" ] || fail "timed out waiting for:${pending}"
    sleep 5
done

for service in $one_shot; do
    code=$(docker compose ps -a --format '{{.ExitCode}}' "$service")
    [ "$code" = "0" ] || fail "one-shot service ${service} exited with ${code}"
done

echo "stack is up"
docker compose ps
