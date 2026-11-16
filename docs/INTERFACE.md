# Engine interface

This file is the contract between the propagation engine and anything that runs it or reads its output. It names no consumer. Anything that can read GeoParquet, a Cloud Optimized GeoTIFF and JSON can use the artifacts; the database is not needed.

Terms: an artifact is one file the engine writes; a run is one invocation of the engine command on one config file; a manifest is the JSON file that lists a run's inputs and artifacts with their checksums.

## The command

One command, one config file:

```
make engine CONFIG=runs/test-tile.yaml
```

or, inside the Docker image, `python -m engine run runs/test-tile.yaml`. The command reads only the config file, the snapshots it names and the fixture or caller-supplied layers it names. It writes only into the output folder named in the config: into `<output_dir>/<run_id>.partial/`, renamed to `<output_dir>/<run_id>/` once the manifest is written, as snapshots are. Nothing deletes a run folder automatically. It exits non-zero and writes no manifest when any step fails, so a folder with a manifest is a complete run and a folder without one is not. A refused endpoint draw (ADR 0006 item 11) is not a failed step: it is dropped for every model and counted in `refused_draws`.

Exit codes: 0 when the run finished; 2 when the config, its inputs or an environment setting were refused before anything was written; 1 when a step failed, leaving `<run_id>.partial/` without a manifest. An existing `<run_id>/` or `<run_id>.partial/` is refused. A config is refused, among other checks, when `run_id` is not lowercase letters, digits and hyphens; a `site_id` is not letters, digits, `.`, `_` and `-` (it names a coverage file); ids repeat; a `box_half_width_m` is negative or not finite; `models` lists `itm` or `p1812` with profiles of mixed polarisation; `hata` is asked for outside 150 to 1500 MHz; a DSM or canopy is named without a 1 m DEM; a DEM or DSM tile is not EPSG:26915, north-up and on whole metres of the run grid, or the DEM does not cover the run extent; the buildings snapshot lacks `buildings.parquet`; or a snapshot role key is missing.

The engine reads these settings from the environment: `SNAPSHOTS_DIR`, with no default; `ITM_SOURCE_COMMIT` when `models` lists `itm`, and `ENGINE_GIT_COMMIT` where there is no git checkout, both with no default; `ITM_LIBRARY_PATH`, default `/usr/local/lib/libitm.so`, the image's library; and `LOG_FORMAT`, default `console`.

## The config file

YAML, loaded with `yaml.safe_load` only, every field explicit, no defaults hidden in code:

| Field | Meaning |
| --- | --- |
| `run_id` | caller's name for the run; becomes the output folder name |
| `frequency_mhz` | a run parameter, never baked into a profile; LongFast is 906.875 |
| `models` | list from {`fspl`, `knife_edge`, `hata`, `itm`, `p1812`}; one column per model in the predictions |
| `tx_profile`, `rx_profile` | names of profile records (below) |
| `tx_sites` | path, relative to the config file, to a caller-supplied point layer (GeoParquet, EPSG:4326 or 26915, `site_id` and geometry, optional `box_half_width_m`) |
| `rx_points` | path, relative to the config file, to a GeoParquet point layer of receivers (`rx_id`, geometry, optional `box_half_width_m`) |
| `snapshots` | a mapping of the roles `dem`, `dsm`, `canopy`, `buildings` and `p1812_maps` to snapshot ids, or null for a role not used; each id is a folder under the `SNAPSHOTS_DIR` setting, not a config field. The roles: DEM always; DSM source (EPT project) and canopy optional; the OpenStreetMap buildings snapshot optional, and required when `models` lists `hata`; the P.1812 map snapshot, or the synthetic map fixture, and a DSM or canopy snapshot, required when `models` lists `p1812` (ADR 0006 decisions 2 and 4). Without the OpenStreetMap snapshot, `tx_building_draws` and `rx_building_draws` are null, never zero, and `hata_variant` is null. Each id is a folder with its manifest present, checked against that manifest before the run; the engine needs no database row |
| `endpoint_draws` | number of position draws for a pair when either end has a `box_half_width_m` above 0; 1 otherwise. A pair with one draw uses the two box centres |
| `seed` | integer seed for every random draw in the run; recorded in the manifest (`snp_01`) |
| `calibration` | optional: path, relative to the config file, to an observations layer (`site_id`, `rx_id`, `observed_loss_db`); when present a calibration artifact is written, predictions are not changed. The acceptance run sets it to the synthetic fixture `fixtures/observations.parquet` |
| `output_dir` | folder that receives `<run_id>/`; a relative path is relative to the config file |

## Profiles

A profile is a named config record under `engine/profiles/<name>.yaml` with: antenna height above ground in metres, antenna gain in dBi, transmit power in dBm where the profile is a transmitter, polarisation, and a free-text `description`. Two ship with the engine:

- `mesh_node`: 2 m, 0 dBi, 22 dBm, vertical. The typical handheld or rooftop Meshtastic node; 22 dBm is the ceiling of the SX1262 radio on most boards. The US region allows 30 dBm, so a caller with a higher-power node sets its own profile.
- `site_gateway`: 10 m, 3 dBi, 22 dBm, vertical. A gateway on a mast or a tall building.

Callers add profiles by adding files. A profile never carries a frequency.

## Artifacts

Every run writes these into `<output_dir>/<run_id>/`:

| Artifact | Format | Content |
| --- | --- | --- |
| `terrain/dem.tif`, and when the config names them `terrain/dsm.tif`, `terrain/canopy.tif`, `terrain/clutter.tif` | COG, EPSG:26915, float32 metres, nodata set | the terrain build for the run's extent; `clutter` is max(dsm minus dem, canopy where nonzero) with the floor at 0 and the fills of ADR 0006 decision 6 and items 6 (a) and 11 (i), the values P.1812's 10 m cells average, and is written only when a DSM or canopy snapshot is named; without a DSM or canopy snapshot, clutter columns are null, never zero. The canopy is resampled onto the run grid by nearest neighbour (Casey, PR #34 comment 6901088171). The run extent is the bounding box of every site and receiver box plus 50 m, rounded out to multiples of 10 m |
| `predictions.parquet` | GeoParquet, EPSG:26915, LINESTRING site to receiver | one row per (site_id, rx_id), with the LINESTRING, distance_m and bearing_deg measured between the two box centres: distance_m, bearing_deg (grid bearing in EPSG:26915, clockwise from grid north), `<model>_db_median` and `<model>_db_iqr` for each model in `models` (fspl, knife_edge, hata, itm, p1812), each a basic transmission loss in dB, antenna gains excluded (ADR 0006); `knife_edge` is free space plus the P.526 Bullington loss (Casey, PR #37), los_share, fresnel_clearance_pct_median, clutter_height_m_median (the 10 m cell average of the ADR 0006 clutter rule along the path, as P.1812 reads it; Casey, PR #42 comment 6906512338. Per draw, the median over the path's interior points; then the median over the draws that have a clutter value), hata_variant (urban when the mean of the two ends' median building share within 100 m, footprint area inside the circle over the circle's area, is at least 0.20; ADR 0006 decision 7.1), `endpoint_draws`, `refused_draws` (draws refused for a missing height or clutter value, an empty 10 m cell or a too-short profile, from 0 to `endpoint_draws`; every median, interquartile range and share comes from the other draws, and all are null when every draw is refused; ADR 0006 item 11; a zero-length pair counts every draw as refused; its bearing_deg, every median, interquartile range and share, clutter_height_m_median and both building draw counts are null, while its ids, distance_m (0), endpoint_draws, refused_draws, seed and, with a buildings snapshot, hata_variant are present), `seed` (the pair's draw seed, from which its draws can be redrawn: `terrain.draws.draw_seed(run seed, key)` with key `json.dumps([site_id, rx_id], separators=(",", ":"))`, then `terrain.draws.draw_endpoints`; a pair with one draw uses the two box centres and no seed; Casey on PR #42), `tx_building_draws` and `rx_building_draws` (of all drawn pairs, how many have their transmitter or receiver end on a building pixel of the 1 m layers; null where the run has no 1 m DEM), `p1812_at_cap_share` (share of the unrefused draws where P.1812's loss is within 0.1 dB of its troposcatter loss; null where `p1812_db_median` is null; ADR 0006 item 2) |
| `coverage_<site_id>_<model>.tif` | COG, EPSG:26915, float32 dB, nodata set | predicted path loss from one site, interpolated linearly (a Delaunay triangulation) between the box centres of the receivers whose `<model>_db_median` is not null, at the centres of 10 m cells over the run extent with cell edges on multiples of 10 m; nodata outside those receivers' hull, and the whole file nodata when fewer than three such receivers exist or they lie on one line; one file per site and model, derived from `predictions.parquet` and never computed separately. Sites are not combined; a caller wanting best-server coverage takes the minimum across files |
| `calibration.parquet`, `calibration.md` | Parquet and Markdown | only when `calibration` is set: per observation `run_id`, `site_id`, `rx_id`, `observed_loss_db`, and per model `<model>_db_predicted` and `<model>_residual_db` (observed minus predicted, so a positive residual means the model predicted less loss than observed); RMSE, MAE, bias and n per model in the Markdown. It is labelled with the run id and never alters `predictions.parquet` |
| `run_report.md` | Markdown | refused draws broken down by cause (missing map data, empty 10 m cell, missing clutter, too-short profile; Casey, PR #30), canopy no-data pixels per canopy tile in the run (Casey, PR #24 comment 6896602098; a corridor's count comes from the run over that corridor), the median DSM filled-pixel count, zero-length and all-refused pairs, and the pairs whose building share (the mean of the two ends' median shares, as for hata_variant) lies in [0.15, 0.25); no column holds these |
| `manifest.json` | JSON | below |

Column names, units and CRS in `predictions.parquet` are fixed by `schema_version`; adding a column bumps the minor version, changing or removing one bumps the major version.

## The manifest

```json
{
  "manifest_version": "1.1.0",
  "schema_version": "1.2.0",
  "engine_version": "0.1.0",
  "run_id": "test-tile",
  "created_at": "2026-11-20T15:04:05Z",
  "config_sha256": "...",
  "seed": 20261108,
  "models": {"fspl": "...", "knife_edge": "...", "hata": "...", "itm": "NTIA ITM 183ad95b", "p1812": "Py1812 6c9061dd, P.1812-8"},
  "engine_git_commit": "<40 hex>",
  "engine_git_dirty": false,
  "itm_library_sha256": "...",
  "itm_source_commit": "<40 hex>",
  "py1812_commit": "6c9061dd022e93c84169059a016643926ce89964",
  "py1812_archive_sha256": "03d7c5bd2bdb831d009266b4a17a2c721f63db4f817f60af47bb82fdc4268469",
  "snapshots": [
    {"id": "3dep-1m-mo-saintlouis-2017-x72y429", "manifest_sha256": "..."},
    {"id": "ept-mo-stlouis-2017-x72y429", "manifest_sha256": "..."},
    {"id": "canopy-v2-0231113110", "manifest_sha256": "..."},
    {"id": "itu-r-p1812-8-202509", "manifest_sha256": "..."},
    {"id": "osm-geofabrik-mo-il-<extract date>", "manifest_sha256": "..."}
  ],
  "inputs": [
    {"path": "fixtures/two_sites.parquet", "sha256": "..."},
    {"path": "fixtures/receivers_500.parquet", "sha256": "..."},
    {"path": "fixtures/observations.parquet", "sha256": "..."}
  ],
  "artifacts": [{"path": "predictions.parquet", "sha256": "...", "content_sha256": "...", "rows": 1000}]
}
```

Rules:

- `manifest_version` follows semantic versioning. A minor bump adds a field; a major bump changes or removes one. A reader that knows 1.x reads every 1.y.
- Manifest 1.1.0 records the code that produced the run (Casey, PR #32): `engine_git_commit`, `engine_git_dirty` (true, false, or null where there is no checkout), `itm_library_sha256` and `itm_source_commit` (null unless `models` lists `itm`), and `py1812_commit` and `py1812_archive_sha256` (null unless it lists `p1812`). `models` has an entry for every model the run lists. Inputs are recorded with the paths the config gives, and profiles as `profiles/<name>.yaml`. `engine_git_dirty` counts untracked files as dirty, apart from the run's own output folder when it lies inside the checkout.
- `schema_version` is the version of the artifact columns; `engine_version` is the package version. Both are recorded so a reader can tell a column change from a numerical change.
- Every input and artifact carries its file sha256, and every artifact also carries a `content_sha256`: the hash of its rows (Parquet, in row order) or its cell values (COG), with GeoParquet's `geo` metadata (the CRS) included, or, for the Markdown files, the file's sha256; not the file hash for Parquet and COG, because Parquet and COG writers embed timestamps. Two runs with the same config, seed and snapshots produce identical `content_sha256` values; that is the reproducibility test (`snp_01`). `make check` and CI run it on a committed 1 km sub-tile fixture (a few megabytes, no network); the full 10 km test tile (about 2.2 to 2.9 GB of EPT streamed through PDAL and not kept, about 0.5 GB kept) is a documented manual acceptance run.
- The manifest is written last, and the `.partial` folder is renamed after it. A folder named `<run_id>/` with a manifest means the run completed.

## Loading without the database

A script in any scratch directory, with `pyarrow` and `rasterio` only, loads a run like this:

```python
import json, pathlib, pyarrow.parquet as pq, rasterio
run = pathlib.Path("out/test-tile")
manifest = json.loads((run / "manifest.json").read_text())
predictions = pq.read_table(run / "predictions.parquet")
coverage = [a["path"] for a in manifest["artifacts"] if a["path"].startswith("coverage_")]
with rasterio.open(run / coverage[0]) as cov:
    print(cov.crs, cov.res, cov.nodata)
```

This snippet is the acceptance test of the interface and is run in CI against the fixture run.

## What the interface does not promise

- No service, API or schedule. The engine is a command.
- No position improvement. Sites and receivers are taken as given, box half-widths included.
- No publication. Outputs stay in the output folder; publishing them is a separate decision under the privacy rules in `docs/data-quality-contract.md`.
