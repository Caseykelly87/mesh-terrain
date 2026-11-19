-- The target's raw, staging, core and analytics schemas exist: on prod from
-- the warehouse init SQL, on a development target from the on-run-start hook
-- (macros/target_schemas.sql). A missing one leaves a source or a later model
-- with nowhere to read or build. It reads pg_namespace, which lists every
-- schema, not information_schema.schemata, which lists only the schemas the
-- current role owns or holds a privilege on: the test role holds none on
-- raw_dev_wh or raw_dev_ta by design (PR #74), and would see them missing.
-- Returns each missing schema.

with expected_schemas as (
    {%- for base_schema in ['raw', 'staging', 'core', 'analytics'] %}
    select '{{ target_schema_name(base_schema) }}'::text as schema_name
    {%- if not loop.last %}
    union all
    {%- endif %}
    {%- endfor %}
)

select expected_schemas.schema_name
from expected_schemas
left join pg_catalog.pg_namespace as namespaces
    on namespaces.nspname = expected_schemas.schema_name
where namespaces.nspname is null
