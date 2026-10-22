# ADR 0004: Keep the pinned MinIO image locally and run the warehouse on PostgreSQL 17 with PostGIS 3.5

Date: 2026-10-22
Status: accepted
Proposed by: platform-eng
Decided by: Casey

## Context

Two facts surfaced while building the Phase 0 stack (docs/PLAN.md, Stack and Phase 0). First, the object store the plan names, MinIO, was archived upstream in 2026: the community server and client repositories are read-only and the images and binaries were withdrawn from Docker Hub, quay.io and dl.min.io. Second, the plan's warehouse pairing of PostgreSQL 16 with PostGIS 3.4 rides on a Debian bullseye image whose apt repositories have moved to archive hosts, and the PostGIS 3.5 upgrade first proposed is not something RDS offers on PostgreSQL 16.

Two terms. RDS (Amazon Relational Database Service) is the managed PostgreSQL that Phase 6 deploys to; it ships a fixed set of extension versions per PostgreSQL minor version, so the local warehouse should match one of those pairings. A digest-pinned image is a container image referenced by the sha256 hash of its content rather than by a tag, so the same bytes are pulled every time even if the tag is later moved or deleted.

## Options

Object store stand-in:

1. Keep `bitnamilegacy/minio`, the last Bitnami build of the final community MinIO release, digest-pinned. Advantage: the plan's tool, the `mc` client and the bucket bootstrap keep working unchanged. Cost: the image is frozen, receives no fixes and may be removed from Docker Hub.
2. Switch to a maintained S3-compatible store (RustFS, SeaweedFS or Garage). Advantage: an upstream that still ships fixes. Cost: a stack-table change and an untested bootstrap path for a component that Phase 6 replaces with S3 anyway.
3. Build MinIO from the archived source in a Dockerfile. Advantage: no third party in the chain. Cost: a slow Go build on every fresh clone, and still unmaintained.

Warehouse version:

1. Stay on PostgreSQL 16 with PostGIS 3.4 (`postgis/postgis:16-3.4`). Advantage: matches RDS for PostgreSQL 16 (16.15 ships PostGIS 3.4.6 and h3-pg 4.2.3). Cost: the image is Debian bullseye and the builder stage needs the Debian and PostgreSQL apt archive hosts; PostGIS 3.4 is the oldest line RDS still ships.
2. PostgreSQL 17 with PostGIS 3.5 (`postgis/postgis:17-3.5`). Advantage: matches RDS for PostgreSQL 17, which ships PostGIS 3.5.x on every minor (3.5.1 from 17.4R2, 3.5.6 from 17.9R2) and h3-pg 4.2.3 from 17.7 (AWS extension list, https://docs.aws.amazon.com/AmazonRDS/latest/PostgreSQLReleaseNotes/postgresql-extensions.html, read 2026-10-22). Cost: a PostgreSQL major bump from what ADR 0001 said; the Debian variant of the image is still bullseye, so the apt archive rewrite stays.
3. PostgreSQL 16 with PostGIS 3.5 (`postgis/postgis:16-3.5`). Advantage: a PostGIS minor bump only. Cost: no RDS for PostgreSQL 16 minor ships PostGIS 3.5, so local and cloud would diverge.

## Decision

Object store option 1 until Phase 6 makes S3 the raw zone, with RustFS (`rustfs/rustfs:1.0.3`) as the untested fallback if the image disappears; warehouse option 2, `postgis/postgis:17-3.5` digest-pinned with h3-pg built at commit a26630b8 (tag v4.2.3).

## Consequences

- ADR 0001 said PostgreSQL 16 and is partly superseded on that point; everything else in it stands.
- Phase 6 Terraform targets RDS for PostgreSQL 17, minor 17.10 or later so PostGIS is 3.5.6 and h3-pg 4.2.3; locally the image carries PostgreSQL 17.5 with PostGIS 3.5.2 and h3-pg 4.2.3, the same minor lines.
- The warehouse Dockerfile keeps the apt archive rewrite in its builder stage, because the Debian variant of `postgis/postgis:17-3.5` is still bullseye; it goes when the image moves to a newer Debian. It also pre-empts h3-pg's `pgindent` formatting step, which PostgreSQL 17's server-dev package would otherwise trigger.
- If the Bitnami image vanishes, the change is the `minio` service in `docker-compose.yml` and possibly the `mc` image in the bootstrap; the pipeline does not change because the raw zone is S3-shaped. Whether `mc` works against RustFS has not been tried.
- Nothing above the database changes: dbt-postgres 1.11.0, psycopg 3.3.6 and psycopg2-binary 2.9.13 (what dbt-postgres uses) all support PostgreSQL 17, as `dbt debug` and `make check` against the stack confirm.
