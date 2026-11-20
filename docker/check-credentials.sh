#!/bin/bash
# Tracing would print each value as it is compared, so it is cleared before
# anything else; make also runs this script with BASH_ENV and SHELLOPTS
# unset, so no startup file or inherited option can turn it back on.
set +o xtrace
# Refuses to start the stack while the owner password or a MinIO root
# credential is empty or one of the three .env.example placeholders: those
# are public, in this repository. make check-credentials runs it, and make up
# and make rotate-owner-password run that first. It reads the values from the
# environment make exports from .env, strips surrounding whitespace and
# quotes, as a hand-edited .env may hold them, compares each value with every
# placeholder, and prints only the name of a variable that fails, never a
# value.
set -euo pipefail

example="$(dirname "$0")/../.env.example"
names=(WAREHOUSE_PASSWORD MINIO_ROOT_USER MINIO_ROOT_PASSWORD)

bare() {
    local text=$1
    text="${text#"${text%%[![:space:]]*}"}"
    text="${text%"${text##*[![:space:]]}"}"
    text="${text#[\"\']}"
    text="${text%[\"\']}"
    printf '%s' "$text"
}

placeholders=()
for name in "${names[@]}"; do
    placeholders+=("$(bare "$(sed -n "s/^${name}=//p" "$example" | tr -d '\r')")")
done

status=0
for name in "${names[@]}"; do
    value=$(bare "${!name-}")
    if [ -z "$value" ]; then
        echo "${name} is not set in .env; generate one (README, Credentials)" >&2
        status=1
        continue
    fi
    for placeholder in "${placeholders[@]}"; do
        if [ "$value" = "$placeholder" ]; then
            echo "${name} in .env is one of the .env.example placeholders; generate one (README, Credentials)" >&2
            status=1
            break
        fi
    done
done
exit "$status"
