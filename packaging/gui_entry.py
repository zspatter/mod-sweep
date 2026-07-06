"""PyInstaller entry point for the windowed executable."""

import sys

from modsweep.gui import main

if __name__ == "__main__":
    sys.exit(main())
