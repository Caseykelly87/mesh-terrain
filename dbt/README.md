# dbt

The `mesh_terrain` dbt project: staging views, core incremental models, analytics marts and the tests that mirror `docs/data-quality-contract.md`. Today the project holds one seed and one view, the MSA boundary (`seeds/README.md`), and the declaration of the raw tables as sources; warehouse-eng writes the rest of the models in Phase 2.

`profiles.yml` is committed and reads the connection from `WAREHOUSE_HOST`, `WAREHOUSE_PORT`, `WAREHOUSE_USER`, `WAREHOUSE_PASSWORD` and `WAREHOUSE_DB`.

## Targets: who builds where

A target is a named set of connection settings in `profiles.yml`. dbt takes it from `--target`; without that flag, from the `DBT_TARGET` environment variable; without either, the profile's default, `dev_wh`. Both targets here connect to the same database. What differs is the schemas, decided in `macros/target_schemas.sql` by the target's name:

| Target | Who runs it | Builds into | Reads raw from |
| --- | --- | --- | --- |
| `dev_wh` (the default) | every agent, through `make check` and `make dbt-build` | `staging_dev_wh`, `core_dev_wh`, `analytics_dev_wh` | `raw_dev_wh`, synthetic fixture tables only |
| `prod` | the lead only, through `make dbt-build-real` | `staging`, `core`, `analytics` | `raw`, the loaded capture |

A worked example: `make check` builds the MSA boundary view as `staging_dev_wh.stg_msa_boundary` and its seed as `staging_dev_wh.msa_boundary`; the view in `staging` is untouched until the lead runs `make dbt-build-real`. A dbt command typed without `--target` also lands in the `_dev_wh` schemas, unless `DBT_TARGET` names `prod`, and then it stops (next paragraph).

Because `DBT_TARGET` can name `prod` without anyone typing it, in a shell, a `.env` or a container, the target alone does not keep a build off the real schemas. Every `dbt build`, `run`, `seed`, `snapshot` or `test` on `prod` stops in its `on-run-start` hook, before any node runs, unless it was given `--vars '{real_build: true}'` with the boolean `true` (a string `'true'` or a `1` is refused). Only `make dbt-build-real` passes that, and `tests/test_dbt_targets.py` fails if any other Makefile recipe does, directly or through a Make variable. Two things happen before the hook: dbt's own schema step issues `create schema if not exists` for the target's schemas, which on `prod` already exist from the init SQL, so a refused run creates nothing. And `dbt clone` runs no `on-run-start` hook at all, so the guard does not cover it: nobody runs `dbt clone` on `prod`. `dbt ls` and `dbt parse` still work on `prod`, since they run no hook.

How it works:

- `generate_schema_name` takes each folder's schema from `dbt_project.yml` (`staging`, `core`, `analytics`) and passes it through `target_schema_name`, which keeps the name on `prod` and appends `_<target name>` on every other target. Test audit tables, used only when a test stores its failures, get the same suffix.
- The `raw` source in `models/staging/raw_sources.yml` names its schema with the same rule written inline, because dbt does not let YAML call project macros. `tests/test_dbt_targets.py` lists both targets with `dbt ls` and fails if any node other than a source on `dev_wh` (a model, seed, snapshot, test or any later type) sits outside a `_dev_wh` schema, if any source on `dev_wh` reads anything but `raw_dev_wh`, or if `prod` lists no model or does not read `raw`. It also fails if any SQL under `models`, `tests`, `snapshots`, `macros` or `analyses` writes `raw.`, `staging.`, `core.`, `analytics.` or `features.` outside Jinja and comments: every relation goes through `ref()` or `source()`, so the target decides its schema.
- The `on-run-start` hook `create_target_schemas` creates `raw_dev_wh`, `staging_dev_wh`, `core_dev_wh` and `analytics_dev_wh` when they are absent, on every target but `prod`. It runs in dbt rather than in the warehouse init SQL because the init SQL runs once, when the data volume is created, so it would never reach the running warehouse or a new target. On `prod` it creates nothing, since the init SQL creates the real schemas, and it holds the refusal above. `tests/assert_target_schemas_exist.sql` fails when one of the target's four schemas is missing.
- `raw_dev_wh` starts empty. Synthetic fixture tables are written into it by the units that need them; nothing copies real raw rows into it.
- A dbt snapshot must set its schema with `schema`, which goes through the macro, not the older `target_schema`, which does not; the target test catches one that does not.

## Running

From the repository root with the stack up and the `.env` variables exported: `make dbt-build` (development target), or `uv run dbt build --project-dir dbt --profiles-dir dbt`, which uses the same default when `DBT_TARGET` is unset. On a fresh worktree run `make dbt-deps` first (`docs/STATUS.md`, Environment notes).

## Contract tests

`tests/test_contract.py` reads every rule id from `docs/data-quality-contract.md` and fails when a rule has no test named for it. It counts only what the contract's opening line names, "a dbt test or a Dagster asset check with the same identifier"; a pytest function does not count, whatever its name. A dbt test names a rule in one of two ways (the contract's naming line, ADR 0007): a singular test file `tests/assert_<rule id>.sql` or `tests/assert_<rule id>_<what>.sql`, `<what>` being a short description of the case, or a generic test in model YAML with the rule id in its meta:

```yaml
columns:
  - name: tx_node_hash
    tests:
      - not_null:
          config:
            meta:
              contract: lnk_08
```

`contract` can also be a list of ids. A Dagster asset check names a rule when its name is the rule id or the rule id followed by `_<what>`, such as `pos_03_not_both_zero` for `pos_03` (Casey, issue #55 comment 6961809084, item 7; ADR 0007). Rules without a test yet are on the `PENDING` list in that file, 27 today; a rule leaves it in the change that adds its test, and the test fails if it stays. `PENDING` must stay inside `PENDING_AT_B1`, the rules pending when the test was written, so the list cannot grow. A new contract rule enters both `PENDING_AT_B1` and `PENDING` in the pull request that adds it to the contract, unless it lands with its test.

A rule the contract enforces by the reviewer alone is on a second list, `REVIEW_ONLY`, never on `PENDING`; today that is `val_02` (Casey, issue #55 comment 6961809084, item 8). It needs no named test. The check fails when a rule is on both lists, when the contract's "Enforced by" cell for a `REVIEW_ONLY` rule names a dbt test or an asset check, when such a rule gains a named test, and when `.claude/agents/reviewer.md` has no checklist line that opens with the rule's id, such as "`val_02`: every headline number ...". `REVIEW_ONLY` must stay inside `REVIEW_ONLY_AT_D12`, so a rule moves to it only through a change to that set and to the contract.

## Layout

`models/staging`, `models/core` and `models/analytics` map one to one onto the warehouse schemas of the same names. Seeds load into `staging` with explicit column types set in `dbt_project.yml`. Every table and view is described in `docs/schema.md`.

Deliberately not here: raw landing (Python loaders write `raw`) and terrain features (Python writes `features`). The development target has no `features` schema yet; a model that reads `features` adds a source with the same target rule as `raw` and a `features_dev_wh` schema in the hook.
