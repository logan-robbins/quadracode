"""Shared runtime core for Quadracode orchestrator and agent services."""

from importlib import import_module
from typing import Any, Dict, Tuple

__all__ = [
    "RuntimeProfile",
    "load_profile",
    "ORCHESTRATOR_PROFILE",
    "AGENT_PROFILE",
    "create_runtime",
    "run_forever",
]


_ATTR_MODULE_MAP: Dict[str, Tuple[str, str]] = {
    "RuntimeProfile": ("profiles", "RuntimeProfile"),
    "load_profile": ("profiles", "load_profile"),
    "ORCHESTRATOR_PROFILE": ("profiles", "ORCHESTRATOR_PROFILE"),
    "AGENT_PROFILE": ("profiles", "AGENT_PROFILE"),
    "create_runtime": ("runtime", "create_runtime"),
    "run_forever": ("runtime", "run_forever"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _ATTR_MODULE_MAP[name]
    except KeyError as exc:  # pragma: no cover - guard against typos
        raise AttributeError(f"module 'quadracode_runtime' has no attribute {name!r}") from exc

    module = import_module(f".{module_name}", __name__)
    value = getattr(module, attribute)
    globals()[name] = value  # Cache for future lookups
    return value
