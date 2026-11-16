"""The terrain-aware path-loss engine: one command from a config file to a run folder.

`python -m engine run <config.yaml>` (`docs/INTERFACE.md`) reads the config,
the snapshots it names and the point layers it names, and writes one run folder
of GeoParquet, Cloud Optimized GeoTIFFs, Markdown and a JSON manifest. The
physics lives in `terrain/`; this package reads and writes files around it.
"""

ENGINE_VERSION = "0.1.0"
"""The engine package version, recorded in every manifest."""

SCHEMA_VERSION = "1.2.0"
"""Version of the artifact columns (`docs/INTERFACE.md`); 1.2.0 adds `seed` (Casey, PR #42)."""

MANIFEST_VERSION = "1.1.0"
"""Version of the manifest's fields; 1.1.0 adds the code provenance fields."""
