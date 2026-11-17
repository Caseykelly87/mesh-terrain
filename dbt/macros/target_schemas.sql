{#
  Which schemas a target builds into. The target prod uses the warehouse
  schemas of the data model (raw, staging, core, analytics) by their own
  names; only the lead runs it. Every other target appends its own name, so
  the default target dev_wh builds staging_dev_wh, core_dev_wh and
  analytics_dev_wh and reads raw_dev_wh. The decision is the target's, not an
  environment variable's, so a build that names no target never reaches the
  real schemas.
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
  The on-run-start hook. dbt creates a model's schema when it builds the
  model, but nothing in dbt builds into raw, and core and analytics have no
  models yet, so a development target creates its four schemas itself and the
  sources and later models always find them. The real schemas come from the
  warehouse init SQL, which runs once per volume, so the hook leaves them
  alone. raw_dev_wh is created empty: only synthetic fixture tables go in it,
  and nothing here copies real rows.
#}
{% macro create_target_schemas() -%}
    {%- if target.name != 'prod' -%}
        {%- for base_schema in ['raw', 'staging', 'core', 'analytics'] %}
    create schema if not exists {{ adapter.quote(target_schema_name(base_schema)) }};
        {%- endfor %}
    {%- endif -%}
{%- endmacro %}
