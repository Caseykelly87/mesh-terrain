# dbt

The `mesh_terrain` dbt project: staging views, core incremental models, analytics marts and the tests that mirror `docs/data-quality-contract.md`. In Phase 0 the project is empty scaffolding; warehouse-eng writes the models in Phase 2.

Run from the repository root with the stack up: `make dbt-build`, or `uv run dbt build --project-dir dbt --profiles-dir dbt` with the `.env` variables exported. `profiles.yml` is committed and reads the connection from `WAREHOUSE_HOST`, `WAREHOUSE_PORT`, `WAREHOUSE_USER`, `WAREHOUSE_PASSWORD` and `WAREHOUSE_DB`.

Layout: `models/staging`, `models/core` and `models/analytics` map one to one onto the warehouse schemas of the same names; `macros/generate_schema_name.sql` keeps the names exact instead of prefixing them with the target schema.

Deliberately not here: raw landing (Python loaders write `raw`), terrain features (Python writes `features`), and the MSA boundary seed, which is a separate unit.
