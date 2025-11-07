"""Pytest configuration for shared fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv


def _load_env_files(paths: Iterable[Path]) -> None:
    """Load local dotenv files without overriding any pre-set environment vars."""
    for env_path in paths:
        if env_path.exists():
            load_dotenv(env_path, override=False)


_REPO_ROOT = Path(__file__).resolve().parent.parent
_load_env_files((_REPO_ROOT / ".env", _REPO_ROOT / ".env.docker"))
