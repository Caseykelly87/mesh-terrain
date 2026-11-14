# pipeline

The Dagster code location for the batch lane: raw loaders, dbt assets, terrain features and marts. Today `pipeline/definitions.py` registers two things: the `raw/mqtt_snapshot` asset, which `ingester/` defines and which loads one registered snapshot into the `raw` schema, and the dbt assets with their CLI resource. `pipeline/common/` holds the typed settings and the structlog setup every asset shares.

Run it locally with `make up` and open http://localhost:3000. On the host, `uv run dagster dev -m pipeline.definitions` works too once `.env` exists. Nothing here is scheduled: there are no schedules or sensors, and an asset runs only when someone materialises it.

Deliberately not here: the capture command and the reduction (`ingester/`, run by hand on the host, never from Dagster; ADR 0005), terrain mathematics (`terrain/`), and SQL transforms (`dbt/`). Assets in this package orchestrate those; they do not reimplement them.
