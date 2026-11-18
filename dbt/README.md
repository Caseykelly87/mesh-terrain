# dbt

The `mesh_terrain` dbt project: staging views, core incremental models, analytics marts and the tests that mirror `docs/data-quality-contract.md`. Today it holds the MSA boundary seed and view (`seeds/README.md`), the raw tables as sources, the staging views `stg_packet` and `stg_position`, and the core table `fct_node_position`; warehouse-eng writes the rest of the models in Phase 2.

`profiles.yml` is committed and reads the connection from `WAREHOUSE_HOST`, `WAREHOUSE_PORT`, `WAREHOUSE_USER`, `WAREHOUSE_PASSWORD` and `WAREHOUSE_DB`.

## Targets: who builds where

A target is a named set of connection settings in `profiles.yml`. dbt takes it from `--target`; without that flag, from the `DBT_TARGET` environment variable; without either, the profile's default, `dev_wh`. All three targets here connect to the same database. What differs is the schemas, decided in `macros/target_schemas.sql` by the target's name:

| Target | Who runs it | Builds into | Reads raw from |
| --- | --- | --- | --- |
| `dev_wh` (the default) | Track B (warehouse-eng), through `make check` and `make dbt-build` | `staging_dev_wh`, `core_dev_wh`, `analytics_dev_wh` | `raw_dev_wh`, synthetic fixture tables only |
| `dev_ta` | Track A (ingest-eng, terrain-rf-eng, platform-eng), through `make check DBT_DEV_TARGET=dev_ta` and `make dbt-build DBT_DEV_TARGET=dev_ta` | `staging_dev_ta`, `core_dev_ta`, `analytics_dev_ta` | `raw_dev_ta`, synthetic fixture tables only |
| `prod` | the lead only, through `make dbt-build-real` | `staging`, `core`, `analytics` | `raw`, the loaded capture |

The two development targets exist so that two `make check` runs at once, one per track, never build into the same schemas (Casey, issue #55 comment 6966197127, item 3). `make check` and `make dbt-build` pass `DBT_DEV_TARGET` to dbt as `--target`, `dev_wh` when it is not set, and refuse any value other than `dev_wh` or `dev_ta`, `prod` included, before any recipe runs.

A worked example: `make check` builds the MSA boundary view as `staging_dev_wh.stg_msa_boundary` and its seed as `staging_dev_wh.msa_boundary`; `make check DBT_DEV_TARGET=dev_ta` builds the same two in `staging_dev_ta`; the view in `staging` is untouched until the lead runs `make dbt-build-real`. A dbt command typed without `--target` also lands in the `_dev_wh` schemas, unless `DBT_TARGET` names `prod`, and then it stops (next paragraph).

Because `DBT_TARGET` can name `prod` without anyone typing it, in a shell, a `.env` or a container, the target alone does not keep a build off the real schemas. Every `dbt build`, `run`, `seed`, `snapshot` or `test` on `prod` stops in its `on-run-start` hook, before any node runs, unless it was given `--vars '{real_build: true}'` with the boolean `true` (a string `'true'` or a `1` is refused). Only `make dbt-build-real` passes that, and `tests/test_dbt_targets.py` fails if any other Makefile recipe does, directly or through a Make variable. Two things happen before the hook: dbt's own schema step issues `create schema if not exists` for the target's schemas, which on `prod` already exist from the init SQL, so a refused run creates nothing. And `dbt clone` runs no `on-run-start` hook at all, so the guard does not cover it: nobody runs `dbt clone` on `prod`. `dbt ls` and `dbt parse` still work on `prod`, since they run no hook.

How it works:

- `generate_schema_name` takes each folder's schema from `dbt_project.yml` (`staging`, `core`, `analytics`) and passes it through `target_schema_name`, which keeps the name on `prod` and appends `_<target name>` on every other target. Test audit tables, used only when a test stores its failures, get the same suffix.
- The `raw` source in `models/staging/raw_sources.yml` names its schema with the same rule written inline, because dbt does not let YAML call project macros. `tests/test_dbt_targets.py` lists every target with `dbt ls` and fails if any node other than a source on a development target (a model, seed, snapshot, test or any later type) sits outside a schema with that target's suffix, if any source on it reads anything but its own raw schema (`raw_dev_wh` or `raw_dev_ta`), or if `prod` lists no model or does not read `raw`. It also fails if any SQL under `models`, `tests`, `snapshots`, `macros` or `analyses` writes `raw.`, `staging.`, `core.`, `analytics.` or `features.` outside Jinja and comments: every relation goes through `ref()` or `source()`, so the target decides its schema.
- The `on-run-start` hook `create_target_schemas` creates the target's four schemas when they are absent, on every target but `prod`: `raw_dev_wh`, `staging_dev_wh`, `core_dev_wh` and `analytics_dev_wh` on `dev_wh`, and the same names ending `_dev_ta` on `dev_ta`. It runs in dbt rather than in the warehouse init SQL because the init SQL runs once, when the data volume is created, so it would never reach the running warehouse or a new target. On `prod` it creates nothing, since the init SQL creates the real schemas, and it holds the refusal above. `tests/assert_target_schemas_exist.sql` fails when one of the target's four schemas is missing.
- `raw_dev_wh` and `raw_dev_ta` start empty and stay empty: the test database role cannot write them (Casey, PR #64, item 5), and nothing copies real raw rows into them. The fixtures go into a throwaway schema instead (Fixture build, below).
- A dbt snapshot must set its schema with `schema`, which goes through the macro, not the older `target_schema`, which does not; the target test catches one that does not.

## Running

From the repository root with the stack up and the `.env` variables exported: `make dbt-build` (Track B's `dev_wh`), `make dbt-build DBT_DEV_TARGET=dev_ta` (Track A), or `uv run dbt build --project-dir dbt --profiles-dir dbt --target <target>`; without `--target` dbt uses `dev_wh` when `DBT_TARGET` is unset. On a fresh worktree run `make dbt-deps` first (`docs/STATUS.md`, Environment notes).

## Fixture build

The models that read raw are tested on synthetic fixtures loaded into a throwaway schema, never into `raw_dev_wh` (Casey, B3 loading path, item 2: "prefer loading the fixture into its own throwaway schema (test_fixture_<random>), pointing dbt sources at it through the existing raw-schema variable, and dropping it at the end, the same pattern as test_load. No exception to the database role.").

- The dbt var `raw_schema`, new with B3, names that schema: `--vars '{raw_schema: test_fixture_0123456789ab}'`. On a development target the raw sources then read it. The value must be `test_fixture_` followed by 12 hex digits, or every dbt command stops while parsing (`macros/raw_schema.sql`, `reads_raw`), so the var cannot point a build at `raw` or `raw_dev_wh`. On `prod` the var is ignored: the sources read `raw` and the build is the same. `tests/test_dbt_raw_schema_var.py` checks all three with `dbt ls`.
- Without the var, a development target builds nothing that reads raw: `stg_packet`, `stg_position`, `stg_neighbor`, `fct_node_position`, `fct_rx_observation`, `int_link_window`, the ephemeral link models, their tests and the source tests are disabled, since `raw_dev_wh` has no tables. So `make dbt-build` and CI's dbt-build job build only the MSA boundary and its tests.
- `tests/test_dbt_fixture_build.py` runs the fixture build. It creates `test_fixture_<12 hex>`, builds the raw tables there from `ingester/sql/raw_tables.sql`, loads `tests/fixtures/link_rules/` and `tests/fixtures/staging_cases/`, and runs dbt with the var: `dbt run --full-refresh` without the second staging snapshot, then `dbt build` after loading it (the incremental path), then `dbt build --full-refresh`. Every node must pass both builds and the two must leave the same core rows. The schema is dropped at the end, after a failure too, with `cascade`, which also drops the staging views on the development target that read it. The test skips where no warehouse answers.
- The seed `seeds/fixtures/expected_fixture_positions.csv` and the singular tests that compare with the fixtures are built only in that build (`fixture_build` in `macros/raw_schema.sql`). Expected results are worked out by hand, never by running B3 code: the seed's quality, 0, 0, precise, MSA and core columns are copied from rows written by hand (`tests/fixtures/staging_cases/`), and its cells and half-widths are computed by `tests/fixture_positions.py` and anchored to the hand-worked boxes of the link-rules cases. After a fixture change, write the hand rows for its new positions first, then regenerate the seed with `uv run python tests/fixture_positions.py`. `tests/test_fixture_positions.py` fails while the seed is stale, while a position has no hand row, and wherever an independent Python computation disagrees with a hand row.
- The link models are compared with the link-rules cases through two more seeds, `seeds/fixtures/expected_link_receipts.csv` and `seeds/fixtures/expected_link_hours.csv`, which `tests/link_rules_expected.py` copies from the cases' hand-worked `expected_receipts` and `expected_link_hours` without computing anything; regenerate them with `uv run python tests/link_rules_expected.py` after a fixture change, and `tests/test_link_rules_expected.py` fails while they are stale. The macros in `macros/link_rules_fixture.sql` do the comparison with the README's tolerances, and the singular tests `assert_lnk_*` call them for the cases each rule covers. `assert_lnk_06_excluded_count` warns on every run with the count of receipts `lnk_06` excludes (Casey, 2026-11-14); the fixture build reads that warning as a pass only when the count is the fixture's 7.

To run it alone: `uv run pytest tests/test_dbt_fixture_build.py` with the stack up and the `.env` variables exported.

## Contract tests

`tests/test_contract.py` reads every rule id from `docs/data-quality-contract.md` and fails when a rule has no test named for it. It lists the project with the var `raw_schema` set, so the tests of the models that read raw count. It counts only what the contract's opening line names, "a dbt test or a Dagster asset check with the same identifier"; a pytest function does not count, whatever its name. A dbt test names a rule in one of two ways (the contract's naming line, ADR 0007): a singular test file `tests/assert_<rule id>.sql` or `tests/assert_<rule id>_<what>.sql`, `<what>` being a short description of the case, or a generic test in model YAML with the rule id in its meta:

```yaml
columns:
  - name: tx_node_hash
    tests:
      - not_null:
          config:
            meta:
              contract: lnk_08
```

`contract` can also be a list of ids. A Dagster asset check names a rule when its name is the rule id or the rule id followed by `_<what>`, such as `pos_03_not_both_zero` for `pos_03` (Casey, issue #55 comment 6961809084, item 7; ADR 0007). Rules without a test yet are on the `PENDING` list in that file, 21 today; a rule leaves it in the change that adds its test, and the test fails if it stays. `PENDING` must stay inside `PENDING_AT_B1`, the rules pending when the test was written, so the list cannot grow. A new contract rule enters both `PENDING_AT_B1` and `PENDING` in the pull request that adds it to the contract, unless it lands with its test.

A rule the contract enforces by the reviewer alone is on a second list, `REVIEW_ONLY`, never on `PENDING`; today that is `val_02` (Casey, issue #55 comment 6961809084, item 8). It needs no named test. The check fails when a rule is on both lists, when the contract's "Enforced by" cell for a `REVIEW_ONLY` rule names a dbt test or an asset check, when such a rule gains a named test, and when `.claude/agents/reviewer.md` has no checklist line that opens with the rule's id, such as "`val_02`: every headline number ...". `REVIEW_ONLY` must stay inside `REVIEW_ONLY_AT_D12`, so a rule moves to it only through a change to that set and to the contract.

## Layout

`models/staging`, `models/core` and `models/analytics` map one to one onto the warehouse schemas of the same names. Seeds load into `staging` with explicit column types set in `dbt_project.yml`. Every table and view is described in `docs/schema.md`.

Deliberately not here: raw landing (Python loaders write `raw`) and terrain features (Python writes `features`). The development targets have no `features` schema yet; a model that reads `features` adds a source with the same target rule as `raw` and a `features_<target>` schema in the hook.
