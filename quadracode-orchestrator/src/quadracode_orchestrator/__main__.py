"""Entry point for the Quadracode orchestrator service.

Starts the orchestrator as a persistent, asynchronous service using ``run_forever``
from ``quadracode_runtime``. Configures logging before launch and handles graceful
shutdown on keyboard interrupt.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from quadracode_runtime.runtime import run_forever

from .profile import PROFILE

logger = logging.getLogger(__name__)


def main() -> None:
    """Launch the orchestrator runtime."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    logger.info("Starting orchestrator with profile=%s", PROFILE.name)
    try:
        asyncio.run(run_forever(PROFILE))
    except KeyboardInterrupt:
        logger.info("Orchestrator shutting down (keyboard interrupt)")
    except Exception:
        logger.exception("Orchestrator crashed")
        raise


if __name__ == "__main__":
    main()
