# pipeline/terrain_snapshots

Commands that write the terrain inputs as snapshots (contract rule `snp_01`): dated folders under `SNAPSHOTS_DIR`, each with a README, a sha256 manifest and the source's licence, never changed once finished. Each command writes into `<id>.partial/` through `pipeline.common.snapshot`, refuses an id that already exists finished or partial, and logs a begin and an end line with its timing and the input checksum.

| Command | Snapshot id | What it keeps |
| --- | --- | --- |
| `dem` | `3dep-1m-mo-saintlouis-2017-x72y429` | the USGS 1 m DEM tile x72y429 and its TNM metadata XML, size, ETag and date checked before download, CRS, pixel size and extent checked after, nodata pixels counted |
| `osm --date 2026-11-01` | `osm-geofabrik-mo-il-2026-11-01` | Geofabrik's Missouri and Illinois extracts of that first-of-month date, md5 checked, and `buildings.parquet` and `water.parquet` read from them with pyrosm inside the St. Louis MSA box (ODbL; credit "© OpenStreetMap contributors") |
| `itu-maps` | `itu-r-p1812-8-202509` | the ITU zip, sha256 checked, and `DN50.TXT` and `N050.TXT`, each sha256 checked; never `P1812.npz` |
| `dsm --osm-snapshot <id> --scratch <dir>` | `ept-mo-stlouis-2017-x72y429` | the 1 m first-return DSM as a COG on the DEM tile's grid, and the PDAL pipeline of each of its 16 windows |
| `canopy` | `canopy-v2-0231113110` | the CHMv2 canopy tile and its imagery-date GeoJSON, size, ETag and date checked, sha256 recorded beside them |

Run one with `python -m pipeline.terrain_snapshots <command>`; `--root <dir>` writes under another folder instead of `SNAPSHOTS_DIR`. The make targets that wrap them are listed in the PR that added this package. Writing a real snapshot is permanent: it runs only after Casey's yes on a checklist.

## The DSM, worked through

The DSM is the lidar surface: roof tops and tree tops. Recipe A1 (Casey, PR #34): first returns only, noise classes 7 and 18 and withheld points dropped, the highest point in each 1 m cell, one-cell gaps between scan lines filled from the neighbouring cells. The tile's 10 012 by 10 012 cells are built as 16 windows of 2 503 by 2 503, each through `make pdal`, each gridded one cell wider on every side so the gap fill at a window's edge sees the next window's cells; only the window's own cells are kept.

Every window is checked so an empty or thin surface never passes. Take window `r0c0`, 2 503 by 2 503 cells, and suppose OpenStreetMap water covers 300 000 of them. Its land is 6 265 009 minus 300 000, 5 965 009 m2. QL2 lidar promises at least 2 pulses a square metre, one first return each, so the window should hold about 11.9 million first returns over land; the command fails it below a quarter of that, about 2.98 million, and on 0. A window with no land at all gets no PDAL run, stays nodata and is recorded as `water_only`. The manifest records each window's land area, water area, point count and nodata cells, and the totals, which are checked the same way.

The command refuses before doing anything when the OSM snapshot is missing or differs from its manifest (write it first with `osm`), when `make check-pdal` reports a version other than `2.8.4 (git-version: 156082)`, or when the scratch folder overlaps the snapshots folder. Window rasters go to `<scratch>/<snapshot id>/` and are not part of the snapshot.

## What this package does not do

It registers nothing in `raw.snapshot`; a loader does that with `register_verified`. It does not resample the canopy (the engine build does, by nearest neighbour), build the clutter layer, or read any snapshot for the engine. It never deletes a `.partial` folder or a scratch folder; Casey decides what happens to one left by a failed run. Its tests use a fake web server and a fake `make`; none touches the network or Docker.
