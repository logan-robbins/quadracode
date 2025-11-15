"""
This module is responsible for constructing and compiling the main LangGraph for 
the Quadracode runtime.

It provides the `build_graph` function, which assembles the various nodes 
(e.g., driver, tools, context engine) into a stateful graph. The module supports 
two modes of operation: a full-featured mode with the context engine enabled, and 
a simpler mode without it. It also handles the configuration of the graph's 
checkpointer, which is responsible for persisting the state of the graph.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3

from langgraph.checkpoint.memory import MemorySaver

try:  # pragma: no cover - optional dependency
    from langgraph.checkpoint.sqlite import SqliteSaver
except ImportError:  # pragma: no cover
    SqliteSaver = None  # type: ignore
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import tools_condition

from .config import ContextEngineConfig
from .nodes.context_engine import ContextEngine
from .nodes.prp_trigger import prp_trigger_check
from .nodes.driver import make_driver
from .nodes.tool_node import QuadracodeTools
from .state import QuadraCodeState, RuntimeState


def _default_checkpoint_path() -> Path:
    """
    Determines the default path for the SQLite checkpoint database.
    """
    explicit = os.environ.get("QUADRACODE_CHECKPOINT_DB")
    if explicit:
        target = Path(explicit)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    candidates = []
    shared_env = os.environ.get("SHARED_PATH")
    if shared_env:
        candidates.append(Path(shared_env))
    candidates.append(Path("/shared"))
    candidates.append(Path.cwd() / ".quadracode")

    for directory in candidates:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except Exception:
            continue
        if os.access(directory, os.W_OK):
            return directory / "checkpoints.sqlite3"

    fallback = Path.cwd()
    return fallback / "checkpoints.sqlite3"


def _build_checkpointer():
    """
    Builds the checkpointer for the graph, using SQLite if available, otherwise 
    falling back to an in-memory checkpointer.
    """
    if SqliteSaver is None:
        return MemorySaver()

    path = _default_checkpoint_path()
    try:
        conn = sqlite3.connect(str(path), check_same_thread=False)
        return SqliteSaver(conn)
    except Exception:
        return MemorySaver()


CHECKPOINTER = _build_checkpointer()
GRAPH_RECURSION_LIMIT = int(os.environ.get("QUADRACODE_GRAPH_RECURSION_LIMIT", "80"))


def build_graph(system_prompt: str, enable_context_engineering: bool = True):
    """
    Constructs and compiles the main LangGraph for the Quadracode runtime.

    This function assembles the graph's nodes and edges, creating a complete, 
    runnable workflow. It can be configured to either include the full context 
    engineering pipeline or to use a simpler, more direct workflow.

    Args:
        system_prompt: The base system prompt for the driver.
        enable_context_engineering: A flag to enable or disable the context 
                                    engineering nodes.

    Returns:
        A compiled LangGraph instance.
    """
    driver = make_driver(system_prompt, QuadracodeTools.tools)

    if enable_context_engineering:
        # Full graph with context engineering
        try:
            config = ContextEngineConfig.from_environment()  # type: ignore[attr-defined]
        except AttributeError:
            config = ContextEngineConfig()
        context_engine = ContextEngine(config)
        workflow = StateGraph(QuadraCodeState)

        # Add nodes
        workflow.add_node("prp_trigger_check", prp_trigger_check)
        workflow.add_node("context_pre", context_engine.pre_process_sync)
        workflow.add_node("context_governor", context_engine.govern_context_sync)
        workflow.add_node("driver", driver)
        workflow.add_node("context_post", context_engine.post_process_sync)
        workflow.add_node("tools", QuadracodeTools)
        workflow.add_node("context_tool", context_engine.handle_tool_response_sync)

        # Add edges
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
        workflow.add_edge("context_post", END)
        workflow.add_edge("context_post", "tools")
        workflow.add_edge("tools", "context_tool")
        workflow.add_edge("context_tool", "driver")
    else:
        # Simple graph without context engineering
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

    return workflow.compile(checkpointer=CHECKPOINTER)
