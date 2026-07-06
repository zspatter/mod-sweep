"""PyInstaller entry point for the console executable."""

import sys

from modsweep.cli import main

if __name__ == "__main__":
    sys.exit(main())
