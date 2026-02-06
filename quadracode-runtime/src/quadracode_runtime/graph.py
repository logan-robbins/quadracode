"""
LangGraph construction and checkpointer factory for the Quadracode runtime.

Provides ``build_graph`` to assemble the stateful LangGraph workflow, and
``create_checkpointer`` to build the appropriate persistence backend:

- **PostgresSaver** (async, pooled) when ``DATABASE_URL`` is set
- **MemorySaver** (in-memory, non-persistent) otherwise

Environment Variables:
    DATABASE_URL: PostgreSQL connection string. When set, the runtime uses
                  ``AsyncPostgresSaver`` with ``psycopg`` async driver and
                  ``psycopg_pool.AsyncConnectionPool`` for production-grade
                  checkpoint persistence that survives restarts.
    QUADRACODE_MOCK_MODE: When "true", forces MemorySaver regardless of
                          DATABASE_URL.
    QUADRACODE_LOCAL_DEV_MODE: When "true", disables persistence requirement.
    QUADRACODE_IN_CONTAINER: Set to "1" when running inside Docker.
    QUADRACODE_GRAPH_RECURSION_LIMIT: Max recursion depth (default 80).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import tools_condition

from .config import ContextEngineConfig
from .nodes.context_engine import ContextEngine
from .nodes.prp_trigger import prp_trigger_check
from .nodes.driver import make_driver
from .nodes.tool_node import QuadracodeTools
from .state import QuadraCodeState, RuntimeState

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver

logger = logging.getLogger(__name__)

_BOOL_TRUE = {"1", "true", "yes", "on"}
_BOOL_FALSE = {"0", "false", "no", "off"}

GRAPH_RECURSION_LIMIT = int(os.environ.get("QUADRACODE_GRAPH_RECURSION_LIMIT", "80"))

# Connection pool sizing for AsyncPostgresSaver
_PG_POOL_MIN_SIZE = int(os.environ.get("QUADRACODE_PG_POOL_MIN_SIZE", "2"))
_PG_POOL_MAX_SIZE = int(os.environ.get("QUADRACODE_PG_POOL_MAX_SIZE", "20"))


def _optional_bool(value: str | None) -> bool | None:
    """Parse a string into a boolean, returning None for empty/unrecognized."""
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized in _BOOL_TRUE:
        return True
    if normalized in _BOOL_FALSE:
        return False
    return None


def is_mock_mode() -> bool:
    """
    Check if mock mode is enabled via QUADRACODE_MOCK_MODE environment variable.

    When mock mode is enabled:
    - Uses MemorySaver checkpointer (ignores DATABASE_URL)
    - Uses in-memory Redis mock (fakeredis) instead of real Redis
    - Allows the service to start without external dependencies

    Returns:
        True if mock mode is enabled, False otherwise.
    """
    return _optional_bool(os.environ.get("QUADRACODE_MOCK_MODE")) is True


def _running_inside_container() -> bool:
    """Detect if we're running inside a Docker container."""
    return Path("/.dockerenv").exists() or os.environ.get("QUADRACODE_IN_CONTAINER") == "1"


def _is_local_dev_mode() -> bool:
    """Determine if we're in local development mode."""
    if is_mock_mode():
        return True
    override = _optional_bool(os.environ.get("QUADRACODE_LOCAL_DEV_MODE"))
    if override is not None:
        return override
    return not _running_inside_container()


def _get_database_url() -> str | None:
    """Return DATABASE_URL if set and non-empty, else None."""
    url = os.environ.get("DATABASE_URL", "").strip()
    return url or None


async def create_checkpointer() -> BaseCheckpointSaver:
    """
    Create the appropriate LangGraph checkpointer based on environment.

    Decision logic:
    1. If mock mode is enabled → MemorySaver (always, ignores DATABASE_URL)
    2. If DATABASE_URL is set → AsyncPostgresSaver with psycopg async pool
    3. Otherwise → MemorySaver (local dev fallback)

    The AsyncPostgresSaver is initialized with:
    - ``psycopg_pool.AsyncConnectionPool`` for connection reuse
    - ``autocommit=True`` and ``row_factory=dict_row`` as required by LangGraph
    - ``prepare_threshold=0`` to disable prepared statements (required for pooling)
    - Automatic table creation via ``.setup()``

    Must be called from within a running async event loop.

    Returns:
        A configured checkpointer instance.

    Raises:
        Falls back to MemorySaver on any initialization error (logged).
    """
    if is_mock_mode():
        logger.info("Mock mode active — using MemorySaver checkpointer")
        return MemorySaver()

    database_url = _get_database_url()

    if database_url:
        try:
            import asyncio

            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from psycopg.rows import dict_row
            from psycopg_pool import AsyncConnectionPool

            open_timeout = float(
                os.environ.get("QUADRACODE_PG_OPEN_TIMEOUT", "30")
            )
            logger.info(
                "Initializing AsyncPostgresSaver "
                "(pool min=%d, max=%d, open_timeout=%.0fs)",
                _PG_POOL_MIN_SIZE,
                _PG_POOL_MAX_SIZE,
                open_timeout,
            )
            pool = AsyncConnectionPool(
                conninfo=database_url,
                open=False,
                min_size=_PG_POOL_MIN_SIZE,
                max_size=_PG_POOL_MAX_SIZE,
                timeout=open_timeout,
                reconnect_timeout=open_timeout,
                kwargs={
                    "autocommit": True,
                    "prepare_threshold": 0,
                    "row_factory": dict_row,
                },
            )
            await asyncio.wait_for(
                pool.open(wait=True, timeout=open_timeout), timeout=open_timeout
            )
            checkpointer = AsyncPostgresSaver(pool)
            await checkpointer.setup()
            logger.info("AsyncPostgresSaver ready — checkpoint tables verified")
            return checkpointer
        except ImportError:
            logger.warning(
                "langgraph-checkpoint-postgres not installed; "
                "falling back to MemorySaver"
            )
        except Exception:
            logger.exception(
                "Failed to initialize AsyncPostgresSaver; "
                "falling back to MemorySaver"
            )

    if not database_url and _running_inside_container():
        logger.warning(
            "Running in container without DATABASE_URL — "
            "checkpoints will NOT survive restarts (MemorySaver)"
        )
    else:
        logger.info("No DATABASE_URL — using MemorySaver (non-persistent)")

    return MemorySaver()


def build_graph(
    system_prompt: str,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    enable_context_engineering: bool = True,
):
    """
    Construct and compile the main LangGraph for the Quadracode runtime.

    Assembles the graph's nodes and edges, creating a complete, runnable
    workflow. Supports a full-featured mode with the context engineering
    pipeline, or a simpler direct workflow.

    Args:
        system_prompt: The base system prompt for the driver LLM.
        checkpointer: The checkpoint saver for state persistence. If None,
                      the graph is compiled without checkpointing (suitable
                      for the LangGraph dev server or stateless invocations).
        enable_context_engineering: Enable the context engineering pipeline
                                    nodes (pre-process, governor, post-process).

    Returns:
        A compiled LangGraph instance.
    """
    driver = make_driver(system_prompt, QuadracodeTools.tools)

    if enable_context_engineering:
        try:
            config = ContextEngineConfig.from_environment()  # type: ignore[attr-defined]
        except AttributeError:
            config = ContextEngineConfig()
        context_engine = ContextEngine(config, system_prompt=system_prompt)
        workflow = StateGraph(QuadraCodeState)

        workflow.add_node("prp_trigger_check", prp_trigger_check)
        workflow.add_node("context_pre", context_engine.pre_process_node)
        workflow.add_node("context_governor", context_engine.govern_context_node)
        workflow.add_node("driver", driver)
        workflow.add_node("context_post", context_engine.post_process_node)
        workflow.add_node("tools", QuadracodeTools)
        workflow.add_node("context_tool", context_engine.handle_tool_response_node)

        workflow.add_edge(START, "prp_trigger_check")
        workflow.add_edge("prp_trigger_check", "context_pre")
        workflow.add_edge("context_pre", "context_governor")
        workflow.add_edge("context_governor", "driver")
        workflow.add_edge("driver", "context_post")
        workflow.add_conditional_edges(
            "context_post",
            tools_condition,
            {"tools": "tools", END: END},
        )
        workflow.add_edge("tools", "context_tool")
        workflow.add_edge("context_tool", "driver")
    else:
        workflow = StateGraph(RuntimeState)
        workflow.add_node("driver", driver)
        workflow.add_node("tools", QuadracodeTools)

        workflow.add_edge(START, "driver")
        workflow.add_conditional_edges(
            "driver",
            tools_condition,
            {"tools": "tools", END: END},
        )
        workflow.add_edge("tools", "driver")

    return workflow.compile(checkpointer=checkpointer)
