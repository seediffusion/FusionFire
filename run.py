#!/usr/bin/env python3
"""Launch Fusion Fire from a source checkout: ``uv run run.py``."""

import sys

from fusionfire.app import main

if __name__ == "__main__":
    sys.exit(main())
