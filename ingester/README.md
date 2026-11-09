# ingester

Turns Meshtastic MQTT deliveries into pseudonymised snapshot Parquet and loads snapshots into `raw` (ADR 0005). Nothing in it is a service and nothing runs unless started.

Modules:

- `keys.py`: the node pseudonym (HMAC-SHA-256 of the node number as 4 bytes big-endian under `NODE_ID_HMAC_KEY`, 64 hex characters), the low id byte, and the delivery key (`"delivery"`, the topic's byte length as 4 bytes big-endian, the UTF-8 topic and the payload). Known-answer tests in `tests/test_keys.py` fix both encodings; changing one after a snapshot exists is a key rotation.
- `topics.py`: topic class, the landed topic (every `!xxxxxxxx` node id replaced by its pseudonym) and the modem preset, by the channel names firmware v2.7.11 gives default channels.
- `decoder.py`: one delivery in, one allowlisted row, internet copy or counted drop out, with the `ing_03` decode status. Default-key decryption only.
- `allowlist.py` and `sql/raw_tables.sql`: the columns that may land and the `raw` tables that hold them; a test keeps the two equal.
- `counts.py` and `sightings.py`: copy, dropped and per-hour per-broker counts, and the broker flags and merged deliveries of `ing_01` and `ing_03`.
- `snapshot.py`, `parquet.py`, `finalise.py`, `readme.py`: the `<id>.partial/` folder, zstd Parquet with the allowlist schema, data content hashes, the merge at exit, coverage, README and the sha256 manifest.
- `reduce.py`: the batch reduction of the 2026-10-22 archive into snapshot `meshtastic-capture-2026-10-22`.
- `capture.py`: the core of the on-demand capture command in `docs/adr/0003-spike/capture.py`.
- `load.py` and `assets.py`: the insert-only loader and the hand-started Dagster asset `raw/mqtt_snapshot` with its `ing_07`, `ing_03` and `ing_04` checks.
- `settings.py`: `NODE_ID_HMAC_KEY`, `SNAPSHOTS_DIR` and the MQTT credentials, read once.

`NODE_ID_HMAC_KEY` is 32 random bytes written as 64 hex characters. Casey generates it with `python -c "import secrets; print(secrets.token_hex(32))"` and keeps its only other copy in his password manager. It is read once, never logged, and the reduction and the capture command refuse to start without it.

Run:

- Tests: `make check` (the loader tests need the local stack and skip without it).
- Reduction: `uv run --frozen python -I -m ingester.reduce`.
- Load: `uv run --frozen python -m ingester.load <snapshot id>`.
- Capture and gate: `docs/adr/0003-spike/README.md`.

Fixtures: `tests/fixtures/mqtt/deliveries.jsonl`, built by `tests/fixtures/mqtt/build.py` from invented ids (`f1c70000` to `f1c7ffff`), invented text and invented positions, re-encrypted with the default key. Rebuild with `uv run python tests/fixtures/mqtt/build.py`; a test fails if the committed file differs from a rebuild.

Deliberately not here: any schedule, sensor or restart policy; any raw payload bytes, text, long or short name or public key in any output; any code that writes into a finished snapshot folder or deletes a `.partial` folder; any `update` or `delete` against `raw`.
