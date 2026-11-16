"""The one exception the engine raises for input it refuses before a run starts.

A ConfigError means the engine wrote nothing: the config, a profile, a point
layer, a named snapshot or the code provenance was refused before the run's
`.partial` folder was created. The command exits with status 2 on it. A
failure after the folder exists is any other exception, exits with status 1
and leaves the folder without a manifest (`docs/INTERFACE.md`). It is not a
ValueError, so no catch of `terrain.errors.ZeroLengthPathError` can take it.
"""


class ConfigError(Exception):
    """The config, a profile, an input layer or a snapshot was refused; nothing was written."""
