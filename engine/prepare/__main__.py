"""Entry point of `python -m engine.prepare`; see `engine.prepare.command`."""

import sys

from engine.prepare.command import main

if __name__ == "__main__":
    sys.exit(main())
