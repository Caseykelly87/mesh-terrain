# terrain

Pure functions for terrain profiles, line of sight, Fresnel clearance, diffraction and propagation models. Nothing here yet; created in Phase 3 by terrain-rf-eng.

Run: the functions are imported by assets in `pipeline/`; tests under `tests/` exercise them on synthetic DEMs.

Deliberately not here: raster downloads, database access, or Dagster objects. Every function takes arrays and numbers and states its CRS.
