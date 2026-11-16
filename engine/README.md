# engine

The terrain-aware path-loss engine of `docs/INTERFACE.md`: one command reads one YAML config and writes one run folder. The physics is in `terrain/`; this package reads the inputs, builds the terrain layers for the run's area, calls `terrain.draws.sample_link` once per site and receiver pair, and writes GeoParquet, Cloud Optimized GeoTIFFs (COGs), Markdown and a JSON manifest.

## Run it

`make engine CONFIG=<file>` runs one config in the engine image, with no network (`docker/engine-run.sh`). It needs `RUNS_HOST_DIR` and `SNAPSHOTS_HOST_DIR`, from `.env` or the command line: the container reads the snapshots read-only at `/snapshots` and the config's folder read-only at `/config`, and writes under `/runs`, so the point layers a config names sit in its folder and its `output_dir` is `/runs` or a folder below it. Before it builds, the script refuses (exit 2) a checkout with uncommitted changes, untracked files included, and the folder and `output_dir` cases listed at its top. CI runs the committed fixture this way:

```
make engine CONFIG=engine/fixtures/fixture-run-itm.yaml \
    SNAPSHOTS_HOST_DIR="$PWD/tests/fixtures/snapshots" RUNS_HOST_DIR=<empty folder outside the checkout>
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

Site and receiver layers are GeoParquet points in EPSG:4326 or EPSG:26915 with `site_id` or `rx_id`, and an optional `box_half_width_m`. A pair with a box at either end gets `endpoint_draws` draws; two exact points get one.

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

## What it does not do

No downloads, no database, no position improvement, no schedule and no publication. It writes only into the configured output folder. The metro DEM in EPSG:4269 is refused until its projection question is decided (`docs/STATUS.md`).
