from __future__ import annotations

import asyncio

from quadracode_runtime.runtime import run_forever

from .profile import PROFILE


def main() -> None:
    asyncio.run(run_forever(PROFILE))


if __name__ == "__main__":
    main()
