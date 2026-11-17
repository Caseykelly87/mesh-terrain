# ingester

Turns Meshtastic MQTT deliveries into pseudonymised snapshot Parquet and loads snapshots into `raw` (ADR 0005). Nothing in it is a service and nothing runs unless started.

Modules:

- `keys.py`: the node pseudonym (HMAC-SHA-256 of the node number as 4 bytes big-endian under `NODE_ID_HMAC_KEY`, 64 hex characters), the low id byte, and the delivery key (`"delivery"`, the topic's byte length as 4 bytes big-endian, the UTF-8 topic and the payload). Known-answer tests in `tests/test_keys.py` fix both encodings; changing one after a snapshot exists is a key rotation.
- `topics.py`: topic class, the landed topic (every `!xxxxxxxx` node id replaced by its pseudonym) and the modem preset, by the channel names firmware v2.7.11 gives default channels.
- `decoder.py`: one delivery in, one allowlisted row, internet copy or counted drop out, with the `ing_03` decode status. Default-key decryption only.
- `allowlist.py` and `sql/raw_tables.sql`: the columns that may land and the `raw` tables that hold them; a test keeps the two equal.
- `counts.py` and `sightings.py`: copy, dropped and per-hour per-broker counts, and the broker flags and merged deliveries of `ing_01` and `ing_03`.
- `snapshot.py`, `parquet.py`, `finalise.py`, `readme.py`: hour file names, zstd Parquet with the allowlist schema, data content hashes, the merge at exit, coverage, README and the capture manifest's run fields and checks. The `<id>.partial/` folder, the sha256 manifest, verification and `raw.snapshot` registration are shared with every snapshot in `pipeline/common/snapshot.py`.
- `reduce.py`: the batch reduction of the 2026-10-22 archive into snapshot `meshtastic-capture-2026-10-22`. Its command is wired to that archive's folders, snapshot id and MeshSTL window. It ran once, on 2026-11-14, and cannot run again because its input was deleted the same day (ADR 0003, Consequences). `reduce_capture` takes any folders of capture JSON lines, and the tests run it on the synthetic fixtures. No future capture goes through it, since ADR 0005 limits the reduction to that one archive and capture runs pseudonymise in memory; `capture.py` imports only its `LICENCE` constant.
- `capture.py`: the core of the on-demand capture command in `docs/adr/0003-spike/capture.py`.
- `load.py` and `assets.py`: the insert-only loader and the hand-started Dagster asset `raw/mqtt_snapshot` with its `ing_07`, `ing_03` and `ing_04` checks.
- `link_pairs.py`: the pairs positioned at both ends in a capture snapshot (the ADR 0003 spike's union, 8 on `meshtastic-capture-2026-10-22`), found by running the spike's `analyze.py` on the snapshot rows, as the reproduction gate does, with the contract's rule for what a position is (`pos_01`, `pos_03`); every node's boxes over the capture, with the `pos_04` precise flag; and the box half-width rule, an inscribed square.
- `tile_selection.py`: the tile's sites and receiver from those pairs, the busiest-pair rule, and the DEM extent check by the engine's rule.
- `engine_inputs.py`: the command that writes the real site and receiver layers for an engine run on one USGS 1 m tile (below).
- `settings.py`: `NODE_ID_HMAC_KEY`, `SNAPSHOTS_DIR` and the MQTT credentials, read once. `SNAPSHOTS_DIR` is also in `pipeline/common/settings.py` with the same default; the ingester keeps its own field so the capture and reduction start without the warehouse variables.

`NODE_ID_HMAC_KEY` is 32 random bytes written as 64 hex characters. Casey generates it with `python -c "import secrets; print(secrets.token_hex(32))"` and a copy is kept off this machine by Casey. It is read once, never logged, and the reduction and the capture command refuse to start without it.

Run:

- Tests: `make check` (the loader tests need the local stack and skip without it).
- Reduction: `uv run --frozen python -I -m ingester.reduce`. It ran once, on 2026-11-14, and cannot run again: its input archive was deleted that day.
- Load: `uv run --frozen python -m ingester.load <snapshot id>`.
- Capture and gate: `docs/adr/0003-spike/README.md`.
- Engine inputs for a tile, from a checkout (it runs the spike scripts under `docs/adr/0003-spike/`), in Git Bash:

  ```
  unset VIRTUAL_ENV
  UV_SYSTEM_CERTS=true uv run --frozen python -I -m ingester.engine_inputs \
    --snapshot meshtastic-capture-2026-10-22 --tile x72y429 \
    --dem-snapshot 3dep-1m-mo-saintlouis-2017-x72y429 --expect-pairs 8 \
    --out C:/Users/Casey/mesh-terrain-data/configs/x72y429
  ```

  It writes `tx_sites_real.parquet` (`site_id`, `geometry`, `box_half_width_m`, `role`: `both_ends_gateway` or `busy_pair_gateway`) and `rx_real.parquet` (`rx_id`, `geometry`, `box_half_width_m`) in EPSG:26915, at each node's stationary box centre. `box_half_width_m` comes from `link_pairs.box_half_width_m`: the smaller, east-west half of the box, so the engine's square is inscribed in it (Casey, issue #44, 1 (b): "an inscribed square that trims the long side"). Both files carry `mesh_terrain:positions` = `real` in their Parquet metadata. Casey, issue #44, 4 (a): ingest-eng's layers "real", the grid and fixtures "synthetic". The lead's interface note on issue #44 (2026-11-16) adds that a layer is `synthetic` only when every row is made up, and any real row makes it `real`. The engine does not read the key yet.
  - Both layers are written into `engine-inputs.partial/` inside the output folder and moved to their names only when both are complete. A refusal (exit 2) writes nothing. Any other failure raises after removing what the run wrote: the layers, the staging folder, and the output folder and parents it created. An existing layer or staging folder is refused and left as it is.
  - It refuses when the pair count differs from `--expect-pairs`, the tile does not hold exactly one pair with both ends and the two busiest pairs by their gateway end (three pairs touched), two pairs tie for second busiest on both link-hours and receipts, a selected node sent more than one box, the DEM snapshot is refused by the engine's reader, the output folder is inside a git checkout, or a layer exists. The two busiest pairs are ranked by link-hours over both runs, ties broken by receipts in those link-hours (Casey, issue #44, engine_inputs Q2 (a), 2026-11-16). Its stdout still shows each pair's link-hours over both runs, public and MeshSTL, and its receipts.
  - Its stdout gives pseudonyms, roles, link-hours, box half-widths, the precise flag and the DEM check per node (the engine's rule on the DEM snapshot's bounds), never a coordinate. Those per-node lines narrow where a node is, so they are read locally. Only the last `counts for issue #44` line is posted (Casey: counts only on issue #44). On a refusal (exit 2), the issue gets only the fact that the command refused; the refusal text goes to Casey in the session, not on the issue.
  - The layers hold real positions, precise MeshSTL ones among them, and are never published (`pos_04`). Casey, issue #44, 2026-11-16: "Coverage outputs built from real gateway positions inherit the rule that precise MeshSTL positions are never published".

Fixtures: `tests/fixtures/mqtt/deliveries.jsonl`, built by `tests/fixtures/mqtt/build.py` from invented ids (`f1c70000` to `f1c7ffff`), invented text and invented positions, re-encrypted with the default key. Rebuild with `uv run python tests/fixtures/mqtt/build.py`; a test fails if the committed file differs from a rebuild.

Deliberately not here: any grid of made-up receivers, run config or path check for the engine (terrain-rf-eng's); any schedule, sensor or restart policy; any raw payload bytes, text, long or short name or public key in any output; any code that writes into a finished snapshot folder or deletes a `.partial` folder; any `update` or `delete` against `raw`.
