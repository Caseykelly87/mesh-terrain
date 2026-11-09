# ingester

Turns Meshtastic MQTT deliveries into pseudonymised snapshot Parquet (ADR 0005). Two things will use it: the batch reduction of the single capture from 2026-10-22 to 2026-10-25 into snapshot `meshtastic-capture-2026-10-22`, and the on-demand capture command in `docs/adr/0003-spike/capture.py`. Nothing in it is a service and nothing runs unless started.

What exists today:

- `keys.py`: the node pseudonym (HMAC-SHA-256 of the node number as 4 bytes big-endian under `NODE_ID_HMAC_KEY`, 64 hex characters), the low id byte, and the delivery key (`"delivery"`, the topic's byte length as 4 bytes big-endian, the UTF-8 topic and the payload). Known-answer tests in `tests/test_keys.py` fix both encodings; changing one after a snapshot exists is a key rotation.
- `topics.py`: topic class (`e`, `json`, `map`, `stat`, `other`), the topic as it lands (every `!xxxxxxxx` node id replaced by its pseudonym) and the modem preset from the channel segment.
- `counts.py` and `sightings.py`: copy and dropped-delivery counts per hour, and the broker flags of a delivery both brokers carried.
- `allowlist.py` and `sql/raw_tables.sql`: the columns that may land and the `raw` tables that hold them; a test keeps the two equal.
- `snapshot.py` and `flush.py`: the `<id>.partial/` folder, the sha256 manifest, the rename at the end, and the flush and stop rules of a capture run.

Not yet here, waiting on the dependencies requested in the decoder unit's PR: the protobuf decoder and its fixtures, the Parquet writer, the reduction, the loader assets and the capture command.

`NODE_ID_HMAC_KEY` is 64 hex characters (32 random bytes), for example from `python -c "import secrets; print(secrets.token_hex(32))"`. It is read once, never logged, and its only copy outside `.env` is in Casey's password manager.

Run the tests: `uv run pytest tests/test_keys.py tests/test_topics.py tests/test_counts.py tests/test_allowlist.py tests/test_snapshot.py tests/test_flush.py`, or `make check`.

Deliberately not here: any schedule, sensor or restart policy; any raw payload bytes, text, long or short name or public key in any output; any code that writes into a snapshot folder after its manifest exists or deletes a `.partial` folder.
