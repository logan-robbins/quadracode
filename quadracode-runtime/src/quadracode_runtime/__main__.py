"""
This module provides a generic entry point for running a Quadracode runtime 
service (e.g., an orchestrator or an agent).

It reads the `QUADRACODE_PROFILE` environment variable to determine which 
profile to load, and then uses the `run_forever` function to start the runtime 
as a persistent, asynchronous service. This allows for a single, configurable 
entry point that can be used to launch any type of Quadracode runtime component, 
which is particularly useful for containerized deployments.

Environment Variables:
    QUADRACODE_PROFILE: Runtime profile (orchestrator, agent, supervisor/human_clone)
    QUADRACODE_MOCK_MODE: Set to "true" for mock mode (no external dependencies)
"""
from __future__ import annotations

import asyncio
import logging
import os

from .logging_utils import configure_logging
from .mock import is_mock_mode_enabled, patch_redis_for_mock_mode
from .profiles import load_profile
from .runtime import run_forever


def main() -> None:
    """Entry point for Quadracode Runtime."""
    configure_logging()
    logger = logging.getLogger(__name__)
    
    # Apply mock mode patches if enabled
    if is_mock_mode_enabled():
        patch_redis_for_mock_mode()
        logger.info(
            "Mock mode enabled - running with simulated dependencies. "
            "No external Redis or MCP services required."
        )
    
    profile_name = os.environ.get("QUADRACODE_PROFILE", "orchestrator")
    logger.info("Loading profile: %s", profile_name)
    
    profile = load_profile(profile_name)
    asyncio.run(run_forever(profile))


if __name__ == "__main__":
    main()
