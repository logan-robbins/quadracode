"""
This module serves as the main entry point for the `quadracode_runtime` package, 
which provides the shared core functionalities for both the orchestrator and 
agent services in the Quadracode ecosystem.

To optimize startup performance and avoid circular dependencies, this package 
employs a lazy loading mechanism using `__getattr__`. The public API of the 
package is exposed through the `__all__` list, and the corresponding objects are 
imported on demand when they are first accessed. This pattern ensures that only 
the necessary modules are loaded into memory, which is particularly beneficial 
in a complex, multi-component system.
"""

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
    """
    Lazily loads attributes from submodules of the `quadracode_runtime` package.

    This function is called by the Python interpreter when an attribute is not 
    found in the module's namespace. It uses a predefined map to determine the 
    correct submodule to import and then retrieves the attribute from that 
    submodule. This allows for a clean public API at the package level while 
    keeping the initial import overhead to a minimum.

    Args:
        name: The name of the attribute to load.

    Returns:
        The requested attribute.

    Raises:
        AttributeError: If the requested attribute is not defined in the 
                        package's public API.
    """
    try:
        module_name, attribute = _ATTR_MODULE_MAP[name]
    except KeyError as exc:  # pragma: no cover - guard against typos
        raise AttributeError(f"module 'quadracode_runtime' has no attribute {name!r}") from exc

    module = import_module(f".{module_name}", __name__)
    value = getattr(module, attribute)
    globals()[name] = value  # Cache for future lookups
    return value
