"""
This module serves as the main entry point for running the Quadracode 
orchestrator service.

It uses the `run_forever` function from the shared `quadracode_runtime` to start 
the orchestrator as a persistent, asynchronous service. The orchestrator's 
behavior is determined by the `PROFILE` object, which is imported from the 
`profile` module. This script allows the orchestrator to be launched as a 
standalone process, ready to manage and coordinate agent activities.
"""
from __future__ import annotations

import asyncio

from quadracode_runtime.runtime import run_forever

from .profile import PROFILE


def main() -> None:
    asyncio.run(run_forever(PROFILE))


if __name__ == "__main__":
    main()
