"""The engine command: `python -m engine run <config.yaml>`.

Exit status 0 for a finished run, 2 when the environment, the config or
anything it names is refused (nothing written), and 1 with the traceback for a failure during the
run (the `.partial` folder stays, without a manifest).
"""

import argparse
import sys
from pathlib import Path

import structlog
from pydantic import ValidationError

from engine.errors import ConfigError
from engine.run import run_engine
from engine.settings import EngineSettings
from pipeline.common.logging import configure_logging

log = structlog.get_logger(component="engine")


def main(argv: list[str] | None = None) -> int:
    """Parse the command line and run the engine once.

    Args:
        argv: Arguments after the program name; the process's by default.

    Returns:
        The exit status.
    """
    parser = argparse.ArgumentParser(prog="python -m engine")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run the engine on one config file")
    run.add_argument("config", type=Path, help="the run's YAML config")
    arguments = parser.parse_args(argv)
    try:
        settings = EngineSettings()
    except ValidationError as err:
        configure_logging("console")
        missing = ", ".join(str(e["loc"][0]).upper() for e in err.errors())
        log.error("engine_settings_refused", reason=f"set {missing} in the environment")
        return 2
    configure_logging(settings.log_format)
    try:
        final = run_engine(arguments.config, settings)
    except ConfigError as err:
        log.error("engine_run_refused", config=str(arguments.config), reason=str(err))
        return 2
    log.info("engine_run_finished", path=str(final))
    return 0


if __name__ == "__main__":
    sys.exit(main())
