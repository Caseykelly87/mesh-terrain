-- Runs once against POSTGRES_DB on first start. Schemas match the data model
-- in docs/PLAN.md and are owned by the application user so dbt can create
-- objects in them without superuser rights.

create schema if not exists raw;
create schema if not exists staging;
create schema if not exists core;
create schema if not exists features;
create schema if not exists analytics;

create extension if not exists postgis;
create extension if not exists h3;
-- h3_postgis lists postgis_raster as a required extension, so cascade installs
-- it. No table stores rasters; the plan's rule against PostGIS raster is about
-- storage, and rasters stay as COGs on disk or S3.
create extension if not exists h3_postgis cascade;
