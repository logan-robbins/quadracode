"""
This module is responsible for loading tools from Model-Centric Programming (MCP)
servers, allowing the Quadracode runtime to dynamically extend its capabilities.

It provides functions to build the MCP server configurations from environment
variables, initialize a `MultiServerMCPClient`, and then load the tools that are
exposed by those servers. The module supports both synchronous and asynchronous
loading, making it compatible with different parts of the runtime. This dynamic
tool loading mechanism is a key feature of the Quadracode system, enabling it to
adapt to different environments and requirements without code changes.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import warnings
from typing import Any, Callable, Dict, List, Protocol
from urllib.parse import urlsplit, urlunsplit

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

try:  # httpx is an optional dependency of langchain; guard import just in case.
    import httpx
except Exception:  # pragma: no cover - fallback path when httpx missing.
    httpx = None  # type: ignore

LOGGER = logging.getLogger(__name__)


class MCPClientProtocol(Protocol):
    async def get_tools(self) -> List[BaseTool]:
        ...


def _parse_int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return max(default, minimum)
    try:
        value = int(raw)
    except ValueError:
        warnings.warn(
            f"Invalid integer for {name}={raw!r}; falling back to {default}.",
            RuntimeWarning,
            stacklevel=2,
        )
        return max(default, minimum)
    return max(value, minimum)


def _parse_float_env(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return max(default, minimum)
    try:
        value = float(raw)
    except ValueError:
        warnings.warn(
            f"Invalid float for {name}={raw!r}; falling back to {default}.",
            RuntimeWarning,
            stacklevel=2,
        )
        return max(default, minimum)
    return max(value, minimum)


_RETRY_ATTEMPTS = _parse_int_env("QUADRACODE_MCP_INIT_RETRIES", 5, minimum=1)
_INITIAL_DELAY = _parse_float_env("QUADRACODE_MCP_INIT_DELAY_SECONDS", 1.0, minimum=0.0)
_MAX_DELAY = _parse_float_env(
    "QUADRACODE_MCP_INIT_MAX_DELAY_SECONDS", 10.0, minimum=_INITIAL_DELAY or 0.0
)
_WARMUP_DELAY = _parse_float_env("QUADRACODE_MCP_INIT_WARMUP_SECONDS", 0.0, minimum=0.0)

_CLIENT: MCPClientProtocol | None = None
_SERVER_CONFIG: Dict[str, Dict[str, Any]] | None = None
_CONFIG_LOGGED = False

_OPTIONAL_SERVER_SIGNATURES: Dict[str, tuple[str, ...]] = {
    "filesystem": (
        "server-filesystem",
        "Secure MCP Filesystem",
        "filesystem",
    ),
    "memory": (
        "server-memory",
        "mcp-server-memory",
        "Knowledge Graph MCP",
    ),
    "perplexity": (
        "server-perplexity",
        "Perplexity Ask",
        "perplexity",
    ),
}


def _require_env(var: str) -> str:
    """
    Retrieves a required environment variable, raising an error if it's not set.
    """
    value = os.environ.get(var)
    if value is None or not value.strip():
        raise RuntimeError(
            f"Missing required environment variable '{var}' for MCP configuration."
        )
    return value


def _ensure_npx_available(server_name: str) -> None:
    """
    Confirms that the `npx` command is available before configuring stdio servers.
    """
    if shutil.which("npx"):
        return
    raise RuntimeError(
        f"Cannot configure MCP '{server_name}' server because 'npx' "
        "was not found on PATH. Install Node.js or remove the server "
        "from the MCP configuration."
    )


def _filesystem_server() -> Dict[str, Any]:
    """Builds the configuration for the filesystem MCP server."""
    _ensure_npx_available("filesystem")
    root = _require_env("SHARED_PATH")
    return {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", root],
        "transport": "stdio",
    }


def _memory_server() -> Dict[str, Any]:
    """Builds the configuration for the in-memory MCP server."""
    _ensure_npx_available("memory")
    return {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"],
        "transport": "stdio",
    }


def _perplexity_server() -> Dict[str, Any] | None:
    """Builds the configuration for the Perplexity MCP server (optional)."""
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key or not api_key.strip():
        return None
    _ensure_npx_available("perplexity")
    return {
        "command": "npx",
        "args": ["-y", "server-perplexity-ask"],
        "transport": "stdio",
        "env": {"PERPLEXITY_API_KEY": api_key},
    }


def _redis_server() -> Dict[str, Any]:
    """Builds the configuration for the Redis MCP server."""
    url = _require_env("MCP_REDIS_SERVER_URL")
    transport = _normalize_redis_transport(_require_env("MCP_REDIS_TRANSPORT"))
    return {"url": url, "transport": transport}


def _normalize_redis_transport(value: str) -> str:
    """
    Normalizes historical transport values to the canonical form expected by
    the MCP client.
    """
    cleaned = value.strip()
    if cleaned == "streamable-http":
        warnings.warn(
            "MCP_REDIS_TRANSPORT=streamable-http is deprecated; "
            "use streamable_http instead. Automatically normalizing "
            "to streamable_http.",
            RuntimeWarning,
            stacklevel=2,
        )
        return "streamable_http"
    return cleaned


_SERVER_BUILDERS: Dict[str, Callable[[], Dict[str, Any] | None]] = {
    "filesystem": _filesystem_server,
    "memory": _memory_server,
    "perplexity": _perplexity_server,
    "redis": _redis_server,
}


def _sanitize_url(value: str) -> str:
    try:
        result = urlsplit(value)
    except ValueError:
        return value
    if result.username or result.password:
        hostname = result.hostname or ""
        netloc = hostname
        if result.port:
            netloc = f"{hostname}:{result.port}"
        return urlunsplit((result.scheme, netloc, result.path, result.query, result.fragment))
    return value


def _summarize_server_config(config: Dict[str, Any]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    transport = config.get("transport")
    if transport:
        summary["transport"] = transport
    if "url" in config:
        summary["url"] = _sanitize_url(str(config["url"]))
    if "command" in config:
        summary["command"] = config["command"]
        if config.get("args"):
            summary["args"] = config["args"]
    if "env" in config:
        summary["env"] = {
            key: ("***" if value else value) for key, value in config["env"].items()
        }
    return summary


def _log_server_config_once(config: Dict[str, Dict[str, Any]]) -> None:
    global _CONFIG_LOGGED
    if _CONFIG_LOGGED:
        return
    if not config:
        LOGGER.warning("No MCP servers configured; skipping MCP tool loading.")
    else:
        summary = {name: _summarize_server_config(cfg) for name, cfg in config.items()}
        LOGGER.warning("MCP server configuration: %s", summary)
    _CONFIG_LOGGED = True


def _build_server_config() -> Dict[str, Dict[str, Any]]:
    """
    Builds the full MCP server configuration by calling all the individual
    server builders.
    """
    config: Dict[str, Dict[str, Any]] = {}
    for name, builder in _SERVER_BUILDERS.items():
        try:
            server_config = builder()
            if server_config is not None:
                config[name] = server_config
        except Exception as e:
            warnings.warn(f"Skipping MCP server '{name}': {e}", RuntimeWarning)
            LOGGER.warning("Skipping MCP server '%s': %s", name, e)
    return config


def _get_server_config() -> Dict[str, Dict[str, Any]]:
    global _SERVER_CONFIG
    if _SERVER_CONFIG is None:
        _SERVER_CONFIG = _build_server_config()
        _log_server_config_once(_SERVER_CONFIG)
    return _SERVER_CONFIG


def _reset_client() -> None:
    global _CLIENT
    _CLIENT = None


async def _ensure_client() -> MCPClientProtocol:
    """
    Initializes and returns a singleton `MultiServerMCPClient` instance.
    """
    global _CLIENT
    if _CLIENT is None:
        config = _get_server_config()
        if not config:
            LOGGER.warning(
                "No MCP servers available; creating a no-op client that returns no tools."
            )

            class _EmptyClient:
                async def get_tools(self) -> List[BaseTool]:
                    return []

            _CLIENT = _EmptyClient()  # type: ignore[assignment]
        else:
            LOGGER.info("Creating MultiServerMCPClient with %s server(s).", len(config))
            _CLIENT = MultiServerMCPClient(config)
    return _CLIENT


def _describe_exception(exc: Exception) -> str:
    if isinstance(exc, ExceptionGroup):
        parts = [_describe_exception(inner) for inner in exc.exceptions[:3]]
        extra = ""
        if len(exc.exceptions) > 3:
            extra = f" (+{len(exc.exceptions) - 3} more)"
        return f"{exc.__class__.__name__}: [{'; '.join(parts)}]{extra}"
    if httpx is not None and isinstance(exc, httpx.HTTPError):
        request = getattr(exc, "request", None)
        if request and request.url:
            return f"{exc.__class__.__name__} while calling {request.url}"
    return f"{exc.__class__.__name__}: {exc}"


def _exception_strings(exc: BaseException) -> List[str]:
    messages: List[str] = []
    stack: List[BaseException] = [exc]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        identifier = id(current)
        if identifier in seen:
            continue
        seen.add(identifier)
        messages.append(f"{current.__class__.__name__}: {current}")
        if isinstance(current, BaseExceptionGroup):
            stack.extend(current.exceptions)
        if current.__cause__:
            stack.append(current.__cause__)
        if current.__context__ and current.__context__ is not current.__cause__:
            stack.append(current.__context__)
    return messages


def _remove_server(server: str, reason: str) -> bool:
    global _SERVER_CONFIG
    config = _SERVER_CONFIG
    if not config or server not in config:
        return False
    config.pop(server, None)
    _reset_client()
    LOGGER.warning("Disabled MCP server '%s' after failure: %s", server, reason)
    return True


def _maybe_disable_optional_servers(exc: Exception) -> bool:
    messages = _exception_strings(exc)
    removed = False
    for server, keywords in _OPTIONAL_SERVER_SIGNATURES.items():
        if not keywords:
            continue
        if any(keyword in message for message in messages for keyword in keywords):
            removed |= _remove_server(server, f"matched keyword {keywords[0]!r}")
    return removed


async def _load_tools_with_retry(initial_client: MCPClientProtocol) -> List[BaseTool]:
    attempts = max(1, _RETRY_ATTEMPTS)
    delay = _INITIAL_DELAY
    max_delay = max(delay, _MAX_DELAY)
    last_exc: Exception | None = None
    client = initial_client

    for attempt in range(1, attempts + 1):
        try:
            LOGGER.info("Loading MCP tools (attempt %s/%s)...", attempt, attempts)
            tools = await client.get_tools()
            LOGGER.info("Loaded %s MCP tool(s).", len(tools))
            return tools
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - need broad catch for retries.
            last_exc = exc
            if _maybe_disable_optional_servers(exc):
                LOGGER.warning(
                    "Retrying MCP tool loading after disabling failing optional servers."
                )
                client = await _ensure_client()
                continue
            description = _describe_exception(exc)
            if attempt == attempts:
                LOGGER.error(
                    "MCP tool loading failed after %s attempt(s): %s",
                    attempts,
                    description,
                    exc_info=exc,
                )
                break
            LOGGER.warning(
                "MCP tool loading failed (attempt %s/%s): %s; retrying in %.2fs",
                attempt,
                attempts,
                description,
                delay,
            )
            await asyncio.sleep(delay)
            delay = min(max_delay, delay * 2 if delay else 1.0)

    if last_exc is not None:
        raise last_exc
    return []


async def aget_mcp_tools() -> List[BaseTool]:
    """
    Asynchronously loads all tools from the configured MCP servers.
    """
    if _WARMUP_DELAY:
        LOGGER.info(
            "Waiting %.2f second(s) before starting MCP initialization.", _WARMUP_DELAY
        )
        await asyncio.sleep(_WARMUP_DELAY)
    client = await _ensure_client()
    return await _load_tools_with_retry(client)


def load_mcp_tools_sync() -> List[BaseTool]:
    """
    Synchronously loads all tools from the configured MCP servers.

    This is a convenience wrapper around `aget_mcp_tools` for use in synchronous
    code.
    """
    import anyio

    return anyio.run(aget_mcp_tools)
