#!/bin/bash
# Refuses to start the stack while the owner password or a MinIO root
# credential is empty or still the .env.example default: those defaults are
# public, in this repository. make up and make rotate-owner-password run it
# first. It reads the values from the environment make exports from .env and
# the defaults from .env.example, compares them, and prints only the name of
# a variable that fails, never a value. It never traces (no set -x).
set -euo pipefail

example="$(dirname "$0")/../.env.example"
status=0
for name in WAREHOUSE_PASSWORD MINIO_ROOT_USER MINIO_ROOT_PASSWORD; do
    default=$(sed -n "s/^${name}=//p" "$example" | tr -d '\r')
    value=${!name-}
    if [ -z "$value" ]; then
        echo "${name} is not set in .env; generate one (README, Credentials)" >&2
        status=1
    elif [ "$value" = "$default" ]; then
        echo "${name} in .env is still the .env.example default; generate one (README, Credentials)" >&2
        status=1
    fi
done
exit "$status"
