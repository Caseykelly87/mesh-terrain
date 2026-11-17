# dbt

The `mesh_terrain` dbt project: staging views, core incremental models, analytics marts and the tests that mirror `docs/data-quality-contract.md`. Today the project holds one seed and one view, the MSA boundary (`seeds/README.md`), and the declaration of the raw tables as sources; warehouse-eng writes the rest of the models in Phase 2.

`profiles.yml` is committed and reads the connection from `WAREHOUSE_HOST`, `WAREHOUSE_PORT`, `WAREHOUSE_USER`, `WAREHOUSE_PASSWORD` and `WAREHOUSE_DB`.

## Targets: who builds where

A target is a named set of connection settings in `profiles.yml`; dbt uses the default one unless a command names another with `--target`. Both targets here connect to the same database. What differs is the schemas, decided in `macros/target_schemas.sql` by the target's name, never by an environment variable:

| Target | Who runs it | Builds into | Reads raw from |
| --- | --- | --- | --- |
| `dev_wh` (the default) | every agent, through `make check` and `make dbt-build` | `staging_dev_wh`, `core_dev_wh`, `analytics_dev_wh` | `raw_dev_wh`, synthetic fixture tables only |
| `prod` | the lead only, through `make dbt-build-real` | `staging`, `core`, `analytics` | `raw`, the loaded capture |

A worked example: `make check` builds the MSA boundary view as `staging_dev_wh.stg_msa_boundary` and its seed as `staging_dev_wh.msa_boundary`; the view in `staging` is untouched until the lead runs `make dbt-build-real`. A dbt command typed without `--target` also lands in the `_dev_wh` schemas.

How it works:

- `generate_schema_name` takes each folder's schema from `dbt_project.yml` (`staging`, `core`, `analytics`) and passes it through `target_schema_name`, which keeps the name on `prod` and appends `_<target name>` on every other target. Test audit tables, used only when a test stores its failures, get the same suffix.
- The `raw` source in `models/staging/raw_sources.yml` names its schema with the same rule written inline, because dbt does not let YAML call project macros. `tests/test_dbt_targets.py` lists both targets with `dbt ls` and fails if any model, seed, snapshot or test on `dev_wh` sits outside a `_dev_wh` schema, if any source on `dev_wh` reads anything but `raw_dev_wh`, or if `prod` does not read `raw`.
- The `on-run-start` hook `create_target_schemas` creates `raw_dev_wh`, `staging_dev_wh`, `core_dev_wh` and `analytics_dev_wh` when they are absent, on every target but `prod`. It runs in dbt rather than in the warehouse init SQL because the init SQL runs once, when the data volume is created, so it would never reach the running warehouse or a new target. On `prod` it does nothing; the init SQL creates the real schemas. `tests/assert_target_schemas_exist.sql` fails when one of the target's four schemas is missing.
- `raw_dev_wh` starts empty. Synthetic fixture tables are written into it by the units that need them; nothing copies real raw rows into it.
- A dbt snapshot must set its schema with `schema`, which goes through the macro, not the older `target_schema`, which does not; the target test catches one that does not.

## Running

From the repository root with the stack up and the `.env` variables exported: `make dbt-build` (development target), or `uv run dbt build --project-dir dbt --profiles-dir dbt`, which uses the same default. On a fresh worktree run `make dbt-deps` first (`docs/STATUS.md`, Environment notes).

## Contract tests

`tests/test_contract.py` reads every rule id from `docs/data-quality-contract.md` and fails when a rule has no test named for it. A dbt test names a rule in one of two ways: a singular test file `tests/assert_<rule id>.sql` or `tests/assert_<rule id>_<what_must_be_true>.sql`, or a generic test in model YAML with the rule id in its meta:

```yaml
columns:
  - name: tx_node_hash
    tests:
      - not_null:
          config:
            meta:
              contract: lnk_08
```

`contract` can also be a list of ids. Rules without a test yet are on the `PENDING` list in that file; a rule leaves it in the change that adds its test, and the test fails if it stays.

## Layout

`models/staging`, `models/core` and `models/analytics` map one to one onto the warehouse schemas of the same names. Seeds load into `staging` with explicit column types set in `dbt_project.yml`. Every table and view is described in `docs/schema.md`.

Deliberately not here: raw landing (Python loaders write `raw`) and terrain features (Python writes `features`). The development target has no `features` schema yet; a model that reads `features` adds a source with the same target rule as `raw` and a `features_dev_wh` schema in the hook.
