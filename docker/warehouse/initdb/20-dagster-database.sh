#!/bin/bash
# Dagster's run and event storage lives in its own database on the same server
# so that dropping or rebuilding the warehouse never loses run history. The
# postgres entrypoint only creates POSTGRES_DB, hence this script.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
    create database "$DAGSTER_PG_DB";
SQL
