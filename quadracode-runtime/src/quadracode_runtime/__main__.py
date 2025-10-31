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
