"""The terrain-aware path-loss engine: one command from a config file to a run folder.

`python -m engine run <config.yaml>` (`docs/INTERFACE.md`) reads the config,
the snapshots it names and the point layers it names, and writes one run folder
of GeoParquet, Cloud Optimized GeoTIFFs, Markdown and a JSON manifest. The
physics lives in `terrain/`; this package reads and writes files around it.
"""

ENGINE_VERSION = "0.2.0"
"""The engine package version, recorded in every manifest; 0.2.0 draws inside
`lnk_02` edge boxes (ADR 0008)."""

SCHEMA_VERSION = "1.2.0"
"""Version of the artifact columns (`docs/INTERFACE.md`); 1.2.0 adds `seed` (Casey, PR #42)."""

MANIFEST_VERSION = "1.2.0"
"""Version of the manifest's fields; 1.1.0 adds the code provenance fields, 1.2.0 each
point layer's `input_schema_version` and `box_form` (ADR 0008, Casey's Q2 (b))."""

INPUT_SCHEMA_VERSION = "1.1.0"
"""The newest point layer schema the engine reads; 1.0.0 is the id, the geometry and
`box_half_width_m`, 1.1.0 adds the four `lnk_02` box edges (ADR 0008). The manifest
records the one each layer's columns use (`engine.points.layer_schema_version`)."""
