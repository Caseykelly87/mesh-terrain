#!/bin/bash
# Creates the raw bucket once the object store accepts connections. The image's
# entrypoint runs a temporary server during setup that answers the health check
# and then restarts, so the first connection attempts can be refused.
set -euo pipefail

connected=false
last_error=""
for attempt in $(seq 1 30); do
    if last_error=$(mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" 2>&1 >/dev/null); then
        connected=true
        break
    fi
    echo "object store not ready (attempt ${attempt}), retrying"
    sleep 2
done

if [ "$connected" != "true" ]; then
    echo "could not reach the object store after 30 attempts: ${last_error}" >&2
    exit 1
fi

mc mb --ignore-existing "local/${RAW_BUCKET}"
mc ls local
