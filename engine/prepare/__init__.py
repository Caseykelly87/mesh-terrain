"""Preparing a 1 m tile run's folder before the engine runs: receivers, checks and config.

`python -m engine.prepare <tile> --folder <folder>` reads the real site and
receiver layers another step wrote into the folder, adds a made-up receiver
grid over the tile (with `--no-real-receivers` it reads no receiver layer
and the grid is the only receivers), refuses or leaves out endpoints the
engine's extent rule would refuse, reports which run paths pass near a cell
more than 60 m above the ground, and writes the run config. It changes
nothing in how the engine runs: the extent rule is the engine's own, called
unchanged. It never runs the engine, and it never prints, logs or writes a
real node's coordinates except into the folder's two point layers, which
stay outside git.
"""
