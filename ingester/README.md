# ingester

The MQTT subscriber service that lands public Meshtastic traffic as hourly Parquet in the raw bucket. Nothing here yet; created in Phase 1 by ingest-eng.

Run: a Compose service added in Phase 1; it runs continuously and is not a Dagster asset.

Deliberately not here: decoding beyond what landing needs, any write to PostGIS, any mutation of landed files.
