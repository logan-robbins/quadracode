"""
This module provides a generic entry point for running a Quadracode runtime 
service (e.g., an orchestrator or an agent).

It reads the `QUADRACODE_PROFILE` environment variable to determine which 
profile to load, and then uses the `run_forever` function to start the runtime 
as a persistent, asynchronous service. This allows for a single, configurable 
entry point that can be used to launch any type of Quadracode runtime component, 
which is particularly useful for containerized deployments.
"""
from __future__ import annotations

import asyncio
import os

from .profiles import load_profile
from .runtime import run_forever


def main() -> None:
    profile_name = os.environ.get("QUADRACODE_PROFILE", "orchestrator")
    profile = load_profile(profile_name)
    asyncio.run(run_forever(profile))


if __name__ == "__main__":
    main()
