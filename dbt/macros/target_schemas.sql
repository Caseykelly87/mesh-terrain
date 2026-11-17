{#
  Which schemas a target builds into. The target prod uses the warehouse
  schemas of the data model (raw, staging, core, analytics) by their own
  names; only the lead runs it. Every other target appends its own name, so
  the default target dev_wh builds staging_dev_wh, core_dev_wh and
  analytics_dev_wh and reads raw_dev_wh. The target comes from --target, or
  from the DBT_TARGET environment variable when no --target is given, or else
  the profile's default, dev_wh. Because DBT_TARGET can pick prod, the target
  alone does not keep a build off the real schemas; create_target_schemas
  below refuses any run on prod that make dbt-build-real did not start.
#}
{% macro target_schema_name(base_schema) -%}
    {%- if target.name == 'prod' -%}
        {{ base_schema | trim }}
    {%- else -%}
        {{ base_schema | trim }}_{{ target.name }}
    {%- endif -%}
{%- endmacro %}

{#
  dbt's default builds "<target schema>_<custom schema>", which would put core
  models in staging_core. A folder's schema from dbt_project.yml is the base
  name instead, suffixed by target_schema_name.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ target_schema_name(custom_schema_name or target.schema) }}
{%- endmacro %}

{#
  The on-run-start hook, so it runs before any node of a build, seed, run,
  snapshot or test, and a failure stops the run with nothing built.

  On prod it refuses unless the run passes --vars '{real_build: true}', the
  boolean true and nothing else that reads as true, which only
  make dbt-build-real does: DBT_TARGET=prod in an agent's shell, in a
  .env or in the Dagster container would otherwise rebuild the real schemas.
  The check waits for execute because dbt also renders hooks while parsing,
  and dbt ls and dbt parse must still work on prod.

  On any other target it creates the target's four schemas. dbt creates a
  model's schema when it builds the model, but nothing in dbt builds into raw,
  and core and analytics have no models yet, so the sources and later models
  always find them. The real schemas come from the warehouse init SQL, which
  runs once per volume, so the hook never creates them. raw_dev_wh is created
  empty: only synthetic fixture tables go in it, and nothing here copies real
  rows.
#}
{% macro create_target_schemas() -%}
    {%- if target.name == 'prod' -%}
        {%- if execute and var('real_build', false) is not sameas true -%}
            {{ exceptions.raise_compiler_error(
                "dbt on the target prod rebuilds the real staging, core and analytics "
                ~ "schemas; only the lead runs it, through make dbt-build-real. Use the "
                ~ "development target dev_wh (make dbt-build) instead."
            ) }}
        {%- endif -%}
    {%- else -%}
        {%- for base_schema in ['raw', 'staging', 'core', 'analytics'] %}
    create schema if not exists {{ adapter.quote(target_schema_name(base_schema)) }};
        {%- endfor %}
    {%- endif -%}
{%- endmacro %}
