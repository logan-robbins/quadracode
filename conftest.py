"""
Project-wide pytest fixtures.

This file re-exports the shared fixtures defined under `tests/conftest.py`
so that they are available to test modules outside the `tests/` tree.
"""

from tests.conftest import *  # noqa: F401,F403

