# Engine interface

This file is the contract between the propagation engine and anything that runs it or reads its output. It names no consumer. Anything that can read GeoParquet, a Cloud Optimized GeoTIFF and JSON can use the artifacts; the database is not needed.

Terms: an artifact is one file the engine writes; a run is one invocation of the engine command on one config file; a manifest is the JSON file that lists a run's inputs and artifacts with their checksums.

## The command

One command, one config file:

```
make engine CONFIG=runs/test-tile.yaml
```

or, inside the Docker image, `python -m engine run runs/test-tile.yaml`. The command reads only the config file, the snapshots it names and the fixture or caller-supplied layers it names. It writes only into the output folder named in the config. It exits non-zero and writes no manifest when any step fails, so a folder with a manifest is a complete run and a folder without one is not.

## The config file

YAML, every field explicit, no defaults hidden in code:

| Field | Meaning |
| --- | --- |
| `run_id` | caller's name for the run; becomes the output folder name |
| `frequency_mhz` | a run parameter, never baked into a profile; LongFast is 906.875 |
| `models` | list from {`fspl`, `knife_edge`, `hata`, `itm`, `p1812`}; one column per model in the predictions |
| `tx_profile`, `rx_profile` | names of profile records (below) |
| `tx_sites` | path to a caller-supplied point layer (GeoParquet, EPSG:4326 or 26915, `site_id` and geometry) |
| `rx_points` | path to a GeoParquet point layer of receivers (`rx_id`, geometry) |
| `snapshots` | ids of the terrain snapshots to use: DEM, DSM source (EPT project), canopy; each must be registered and its manifest present |
| `endpoint_draws` | number of position draws when a site or receiver carries `box_half_width_m`; 1 for points |
| `calibration` | optional: path to an observations layer; when present a calibration artifact is written, predictions are not changed |
| `output_dir` | folder that receives `<run_id>/` |

## Profiles

A profile is a named config record under `engine/profiles/<name>.yaml` with: antenna height above ground in metres, antenna gain in dBi, transmit power in dBm where the profile is a transmitter, polarisation, and a free-text `description`. Two ship with the engine:

- `mesh_node`: 2 m, 0 dBi, 22 dBm, vertical. The typical handheld or rooftop Meshtastic node; power is the LongFast default in the US.
- `site_gateway`: 10 m, 3 dBi, 22 dBm, vertical. A gateway on a mast or a tall building.

Callers add profiles by adding files. A profile never carries a frequency.

## Artifacts

Every run writes these into `<output_dir>/<run_id>/`:

| Artifact | Format | Content |
| --- | --- | --- |
| `terrain/dem.tif`, `terrain/dsm.tif`, `terrain/canopy.tif`, `terrain/clutter.tif` | COG, EPSG:26915, float32 metres, nodata set | the terrain build for the run's extent; `clutter` is max(dsm minus dem, canopy where nonzero) |
| `predictions.parquet` | GeoParquet, EPSG:26915, LINESTRING site to receiver | one row per (site_id, rx_id): distance_m, bearing_deg, `<model>_db` median and `<model>_db_iqr` per model, los_share, fresnel_clearance_pct_median, knife_edge_db_median, clutter_height_m_median, hata_variant, `endpoint_draws` |
| `coverage_<model>.tif` | COG, EPSG:26915, float32 dB, nodata set | predicted path loss from each site at each receiver point interpolated to the run's grid; one per model, derived from `predictions.parquet` and never computed separately |
| `calibration.parquet`, `calibration.md` | Parquet and Markdown | only when `calibration` is set: per observation, predicted and observed, residual per model; RMSE, MAE, bias and n per model in the Markdown. It is labelled with the run id and never alters `predictions.parquet` |
| `manifest.json` | JSON | below |

Column names, units and CRS in `predictions.parquet` are fixed by `schema_version`; adding a column bumps the minor version, changing or removing one bumps the major version.

## The manifest

```json
{
  "manifest_version": "1.0.0",
  "schema_version": "1.0.0",
  "engine_version": "0.1.0",
  "run_id": "test-tile",
  "created_at": "2026-11-20T15:04:05Z",
  "config_sha256": "...",
  "models": {"itm": "NTIA ITM 1.4", "p1812": "Py1812 6c9061dd, P.1812-8"},
  "snapshots": [{"id": "3dep-1m-mo-saintlouis-2017", "manifest_sha256": "..."}],
  "inputs": [{"path": "fixtures/two_sites.parquet", "sha256": "..."}],
  "artifacts": [{"path": "predictions.parquet", "sha256": "...", "rows": 1000}]
}
```

Rules:

- `manifest_version` follows semantic versioning. A minor bump adds a field; a major bump changes or removes one. A reader that knows 1.x reads every 1.y.
- `schema_version` is the version of the artifact columns; `engine_version` is the package version. Both are recorded so a reader can tell a column change from a numerical change.
- Every input and artifact carries its sha256. Two runs with the same config and snapshots produce identical artifact checksums; that is the reproducibility test and `make check` runs it on the fixture.
- The manifest is written last. Its presence means the run completed.

## Loading without the database

A script in any scratch directory, with `pyarrow` and `rasterio` only, loads a run like this:

```python
import json, pathlib, pyarrow.parquet as pq, rasterio
run = pathlib.Path("out/test-tile")
manifest = json.loads((run / "manifest.json").read_text())
predictions = pq.read_table(run / "predictions.parquet")
with rasterio.open(run / "coverage_itm.tif") as cov:
    print(cov.crs, cov.res, cov.nodata)
```

This snippet is the acceptance test of the interface and is run in CI against the fixture run.

## What the interface does not promise

- No service, API or schedule. The engine is a command.
- No position improvement. Sites and receivers are taken as given, box half-widths included.
- No publication. Outputs stay in the output folder; publishing them is a separate decision under the privacy rules in `docs/data-quality-contract.md`.
