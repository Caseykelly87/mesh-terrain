{#
  dbt's default builds "<target schema>_<custom schema>", which would put core
  models in staging_core. The warehouse schemas are fixed by the plan and
  created by the database init SQL, so a custom schema name is used verbatim.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
