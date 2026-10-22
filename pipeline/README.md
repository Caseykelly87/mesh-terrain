# pipeline

The Dagster code location for the batch lane: raw loaders, dbt assets, terrain features and marts. In Phase 0 it holds the dbt resource and the dbt assets only; `pipeline/common/` holds the typed settings and the structlog setup every asset shares.

Run it locally with `make up` and open http://localhost:3000. On the host, `uv run dagster dev -m pipeline.definitions` works too once `.env` exists.

Deliberately not here: the MQTT subscriber (a long-running service under `ingester/`), terrain mathematics (`terrain/`), and SQL transforms (`dbt/`). Assets in this package orchestrate those; they do not reimplement them.
