# ADR 0001: Use Dagster, dbt and PostGIS on Docker Compose, with Terraform to AWS later

Date: 2026-10-20
Status: accepted
Proposed by: lead
Decided by: Casey

## Context

The pipeline needs an orchestrator, a SQL modelling layer and a spatial database, and it needs to run on a laptop first and on a cheap cloud host later. The choice shapes what the repository demonstrates to a hiring reviewer as much as what it does.

## Options

1. Dagster + dbt + PostGIS. Asset-based orchestration with native dbt integration; light to run locally; strong current demand. Less common in older job postings than Airflow.
2. Airflow + dbt + PostGIS. Most-listed orchestrator in postings. Heavier local footprint, more boilerplate, weaker lineage for dbt.
3. Prefect + dbt + PostGIS. Lightest to run. Least common in postings.

For the spatial store, PostGIS was the only serious option; it is what utilities, counties and most GIS developer postings in the St. Louis market name. Rasters stay as Cloud Optimised GeoTIFFs on disk or S3 and are read with rasterio rather than loaded into PostGIS raster.

For infrastructure, Docker Compose locally with a single EC2 host plus RDS and S3 under Terraform in Phase 6. ECS or Kubernetes were rejected as cost and complexity without a matching demonstration benefit.

## Decision

Option 1. Dagster for orchestration, dbt-postgres for transforms, PostgreSQL 16 with PostGIS 3.4 as the warehouse, MinIO locally standing in for S3, Terraform for the cloud phase.

## Consequences

- dbt models appear as Dagster assets with lineage from the raw loader; tests surface as asset checks.
- The raw zone is S3-shaped from day one, so the cloud move is a configuration change.
- Airflow familiarity is not demonstrated; the README names the trade-off in one line.
- Everything must run in Compose on a laptop with 16 GB of memory; the 1 m DEM work is windowed to respect that.
