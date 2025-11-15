"""
Configuration for the pytest framework.

This file modifies the system path to include the `src` directory of the
`quadracode-ui` package, allowing tests to import the application modules
directly.
"""
from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
