{#
  Whether the models and tests that read raw are built, and the guard on the
  var raw_schema.

  On prod the raw sources read raw and the var raw_schema is ignored, so
  nothing passed on the command line can point a real build at other tables
  (models/staging/raw_sources.yml). On a development target the sources read
  raw_<target> unless the run passes --vars '{raw_schema: test_fixture_<12
  hex>}', the throwaway schema a test loaded the synthetic fixtures into
  (Casey, B3 loading path, item 2; the pattern of tests/raw_schema.py).

  raw_dev_wh and raw_dev_ta hold no tables, and the test database role cannot
  write them (Casey, PR #64, item 5), so on a development target the models
  and tests that read raw are built only when the var is set;
  tests/test_dbt_fixture_build.py runs that build. Every model that reads raw
  calls reads_raw() in its config, which runs while dbt parses the project, so
  a var that is not a throwaway fixture schema name stops every dbt command
  before anything is built: the var cannot point a development build at raw
  or raw_dev_wh. dbt does not offer project macros where it renders source
  YAML, which is why the schema choice itself is written there inline.
#}
{% macro reads_raw() -%}
    {%- set requested = var('raw_schema', none) -%}
    {%- if target.name == 'prod' -%}
        {{ return(true) }}
    {%- elif requested is none -%}
        {{ return(false) }}
    {%- elif modules.re.fullmatch('test_fixture_[0-9a-f]{12}', requested | string) -%}
        {{ return(true) }}
    {%- else -%}
        {{ exceptions.raise_compiler_error(
            "raw_schema must name a throwaway fixture schema, test_fixture_ followed by "
            ~ "12 hex digits, not '" ~ requested ~ "'."
        ) }}
    {%- endif -%}
{%- endmacro %}

{#
  Whether this run builds on the synthetic fixtures: a development target
  with raw_schema set. The tests that compare models with the fixtures'
  expected values, and the seed holding those values, are built only then.
#}
{% macro fixture_build() -%}
    {{ return(target.name != 'prod' and reads_raw()) }}
{%- endmacro %}
