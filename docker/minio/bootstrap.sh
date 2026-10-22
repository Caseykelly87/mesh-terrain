#!/bin/bash
# Creates the raw bucket once the object store accepts connections. The image's
# entrypoint runs a temporary server during setup that answers the health check
# and then restarts, so the first connection attempts can be refused.
set -euo pipefail

for attempt in $(seq 1 30); do
    if mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1; then
        break
    fi
    echo "object store not ready (attempt ${attempt}), retrying"
    sleep 2
done

mc mb --ignore-existing "local/${RAW_BUCKET}"
mc ls local
