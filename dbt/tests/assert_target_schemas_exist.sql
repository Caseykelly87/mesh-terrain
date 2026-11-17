-- The target's raw, staging, core and analytics schemas exist: on prod from
-- the warehouse init SQL, on a development target from the on-run-start hook
-- (macros/target_schemas.sql). A missing one leaves a source or a later model
-- with nowhere to read or build. Returns each missing schema.

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
left join information_schema.schemata
    on schemata.schema_name = expected_schemas.schema_name
where schemata.schema_name is null
