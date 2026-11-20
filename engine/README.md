# engine

The terrain-aware path-loss engine of `docs/INTERFACE.md`: one command reads one YAML config and writes one run folder. The physics is in `terrain/`; this package reads the inputs, builds the terrain layers for the run's area, calls `terrain.draws.sample_link` once per site and receiver pair, and writes GeoParquet, Cloud Optimized GeoTIFFs (COGs), Markdown and a JSON manifest.

## Run it

`make engine CONFIG=<file>` runs one config in the engine image, with no network (`docker/engine-run.sh`). It needs `RUNS_HOST_DIR` and `SNAPSHOTS_HOST_DIR`, from `.env` or the command line: the container reads the snapshots read-only at `/snapshots` and the config's folder read-only at `/config`, and writes under `/runs`, so the point layers a config names sit in its folder or below it and its `output_dir` is `/runs` or a folder below it. Before it builds, the script refuses (exit 2) a checkout with uncommitted changes, untracked files included, and the folder and `output_dir` cases listed at its top. CI runs the committed fixture this way:

```
make engine CONFIG=engine/fixtures/fixture-run-itm.yaml \
    SNAPSHOTS_HOST_DIR="$PWD/tests/fixtures/snapshots" RUNS_HOST_DIR=<a folder that neither holds nor lies inside a checkout or a snapshots folder>
```

The committed fixture configs write to `/runs`, the container path; on Windows that path is `C:\runs`. On the host, run a copy of the config with another `output_dir`, as `tests/engine_fixture.py` writes one:

```
SNAPSHOTS_DIR=tests/fixtures/snapshots uv run --frozen python -m engine run <copy of the config>
```
The environment says where things are; the config says what to compute:

| Variable | Meaning |
| --- | --- |
| `SNAPSHOTS_DIR` | required: folder whose subfolders are the snapshot ids a config names; `tests/fixtures/snapshots` for the fixture |
| `ITM_LIBRARY_PATH` | NTIA's `libitm.so`; needed only when `models` lists `itm` |
| `ITM_SOURCE_COMMIT` | the NTIA commit the library was built from; needed with `itm` |
| `ENGINE_GIT_COMMIT` | the engine's commit where there is no git checkout (the image) |
| `ENGINE_GIT_DIRTY` | `true` or `false`: whether that checkout had changes, where there is no git checkout (the image); `make engine` sets it, and the engine refuses any other value |
| `LOG_FORMAT` | `console` or `json` |

Exit status: 0 for a finished run; 2 when the environment (an unset `SNAPSHOTS_DIR`), the config or anything it names is refused, with nothing written; 1 for a failure during the run, which leaves `<run_id>.partial/` without a manifest. Nothing deletes a run folder.

## The config

Every field of `docs/INTERFACE.md` is required, null where unused. Paths are relative to the config file. `snapshots` maps five roles to snapshot ids:

```yaml
snapshots:
  dem: dem-1m-synthetic-subtile        # always
  dsm: dsm-synthetic-subtile           # or null
  canopy: canopy-synthetic-subtile     # or null
  buildings: osm-buildings-synthetic   # required by hata; holds buildings.parquet
  p1812_maps: p1812-maps-synthetic     # required by p1812, with a dsm or canopy
```

Site and receiver layers are GeoParquet points in EPSG:4326 or EPSG:26915 with `site_id` or `rx_id`, and an optional position box, never in both forms: `box_half_width_m`, a square on the grid, or the four `lnk_02` edges `box_south_i`, `box_north_i`, `box_west_i`, `box_east_i` (whole numbers in 1e-7 degrees, ADR 0008), with the point at the box centre to 1 mm and all four null on an exact point. A pair with a box at either end gets `endpoint_draws` draws; two exact points get one. No draw uses a half-width below the config's `position_floor_m`, the GPS error floor, except an exact point, which stays exact (ADR 0009; Casey, issue #55 comment 7043900875, item 1; 5.0 in prepared runs and the fixtures, 0 turns it off), and the run extent holds the widened boxes. The manifest names each point layer's `input_schema_version` and `box_form` (`rectangle`, `square` or `point`). The engine admits a layer whose positions marker is `synthetic`, or `real` with the `prepared.json` that `engine.prepare` writes beside it listing the layer's sha256; an unmarked layer, a real layer copied away from that file and a real layer changed after prepare are refused. Set the marker with `engine.positions_marker.stamp_positions(path, "synthetic")` for a made-up layer. This stops accidents, not deliberate bypass (Casey, issue #55 comment 7024307502, item 2, Q1 (a)): a hand-written `prepared.json` in prepare's format listing a layer's name and sha256, or a real layer re-marked `synthetic`, still reaches the engine.

## What a run does

1. Checks everything before writing: the config and profiles, every named snapshot against its sha256 manifest, the point layers, the observations, that the run grid lies inside the DEM, the code provenance, and that neither `<run_id>/` nor `<run_id>.partial/` exists.
2. Builds `terrain/dem.tif`, `dsm.tif`, `canopy.tif` and `clutter.tif` on the run grid: the bounding box of every position box plus 50 m, with edges on multiples of 10 m so P.1812's 10 m cells line up. DEM and DSM pixels are copied; the canopy is warped by nearest neighbour onto the 1 m grid; clutter follows `terrain.clutter`.
3. Per pair: the draws, each end's building share within 100 m (polygon area in a circle), the Okumura-Hata variant from the two medians, then `sample_link` with the models and the geometry (`engine/quantities.py`).
4. Writes `predictions.parquet`, a coverage COG per site and model interpolated from it at 10 m, the calibration files when the config names observations, `run_report.md`, and `manifest.json` last; then renames the folder.

A worked example from the fixture run: 1000 pairs, 2500 draws, 88 refused (25 missing map data and 58 empty 10 m cells around the made-up DEM hole, 5 too-short profiles), 35 pairs with every draw refused and so null in every model column.

`clutter_height_m_median` reads the same clutter as `terrain/clutter.tif` and P.1812: `terrain.clutter.clutter_height_m` (the ADR 0006 rule with its floor and fills) per 1 m pixel, averaged into P.1812's 10 m cells; per draw the median over the interior points of P.1812's profile, then the median over draws. A short path whose 10 m profile has no interior point is refused when the run lists p1812, and otherwise keeps its losses with no clutter for that draw. `seed` holds each pair's `draw_seed(run_seed, link_id)`, with `link_id` the JSON list `[site_id, rx_id]` (Casey, PR #42).

## Reproducibility

Each artifact's `content_sha256` hashes its rows or cells, not the file (`engine/hashing.py`), and two runs of one config give the same values. The manifest's `engine_git_commit`, `engine_git_dirty`, `itm_library_sha256`, `itm_source_commit`, `py1812_commit` and `py1812_archive_sha256` name the code (`engine/provenance.py`). `engine_git_dirty` counts tracked changes and untracked files, except the run's own `output_dir` when it lies inside the checkout, so a run written there does not mark the next one dirty.

## Fixtures

`engine/fixtures/` holds the fixture configs and point layers; `tests/fixtures/snapshots/` holds the synthetic 1 km sub-tile (DEM, DSM, two canopy tiles), the buildings layer and the synthetic P.1812 maps. All are made up by `tests/make_engine_fixtures.py`; none is real data, and P.1812 numbers from them are not predictions.

## Preparing a tile run

`python -m engine.prepare x72y429 --folder <configs folder>` writes a real run's inputs before `make engine`, with `SNAPSHOTS_DIR` set as for the engine. The folder already holds the real gateways (`tx_sites_real.parquet`: `site_id`, `geometry`, the box, `role`) and the both-ends pair's relay end (`rx_real.parquet`: `rx_id`, `geometry`, the box), in EPSG:26915, the box as `box_half_width_m` or as the four `lnk_02` edges, which it carries into the run's layers with null edges on the grid points (ADR 0008), written by ingest-eng's command. A real row without a box is refused, exit 2 and nothing written: in a layer with the edge columns a row whose four edges are null (Casey, PR #73), in a square layer a row of half-width 0 (Casey, issue #55 comment 7024307502, item 2, Q3 (a)). It adds 400 made-up receivers every 500 m starting 250 m inside the tile edges (`grid-rRRcCC`, rows from the north, columns from the west, box 0). It checks each box plus 50 m against the DEM snapshot with the engine's own extent rule: a receiver that fails stops it, a site that fails is left out. It finds the DSM cells more than 60 m above the DEM and measures each site to receiver path's distance to them (Casey's Q2 (a), issue #44). Then it writes `rx_points.parquet`, `tx_sites.parquet`, `crossing.md`, `prepared.json` (the two layers' names and sha256, `engine/prepared.py`) and `x72y429.yaml` into a temporary folder, checks the config with the engine's `load_config`, moves the five files to their names, and prints `crossing.md`. Exit status 0 when written, 2 when refused, 1 for a failure while writing; a refusal or failure leaves the folder as it was; it never overwrites one of its outputs and refuses a folder inside a git checkout. Without `rx_real.parquet` it refuses, unless it runs with `--no-real-receivers`, for a run whose receivers are the grid alone (Casey, engine_inputs (c), 2026-11-17, for x72y429; the flag is refused for any other tile). With that flag the folder holds only `tx_sites_real.parquet` (ingest-eng's `--sites-only`), and the command refuses, exit 2 and nothing written, a folder that also holds `rx_real.parquet`, so a folder prepared for a run with a real receiver never loses it silently. `rx_points.parquet` is then the 400 grid points, stamped `synthetic`; `tx_sites.parquet` keeps the marker of the sites layer; the extent check, the sites left out and the config are as without the flag; and the real-endpoint section of `crossing.md` says the run has no real receiver, so it lists site to grid paths only. Its messages, log lines and `crossing.md` name nodes by pseudonym and hold no coordinate. `crossing.md` and the printed summary list box reaches (centre to farthest corner), so they are read locally only and never posted; issue #44 gets counts only, since the repository is public. The folder holds real positions and stays outside git. Coverage outputs built from real gateway positions inherit the rule that precise MeshSTL positions are never published (Casey, issue #44). The config puts `mesh_node` at both ends (Casey, issue #44, decision 2 (a)). The two point layers carry the positions marker `mesh_terrain:positions` in their Parquet file metadata, `real` unless every input row is marked `synthetic` (`engine/positions_marker.py`; Casey, issue #44, item 4 (a)); the engine admits a layer marked `synthetic`, or marked `real` and listed with its current sha256 in the `prepared.json` beside it, and refuses an unmarked one. The committed fixture layers in `engine/fixtures/` are stamped `synthetic`.

## What it does not do

No downloads, no database, no position improvement, no schedule and no publication. An engine run writes only into the configured output folder. The metro DEM in EPSG:4269 is refused until its projection question is decided (`docs/STATUS.md`).
