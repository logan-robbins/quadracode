from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import tools_condition

from .config import ContextEngineConfig
from .nodes.context_engine import ContextEngine
from .nodes.driver import make_driver
from .nodes.tool_node import QuadracodeTools
from .state import ContextEngineState, RuntimeState


CHECKPOINTER = MemorySaver()


def build_graph(system_prompt: str, enable_context_engineering: bool = True):
    driver = make_driver(system_prompt, QuadracodeTools.tools)

    if enable_context_engineering:
        context_engine = ContextEngine(ContextEngineConfig())
        workflow = StateGraph(ContextEngineState)

        workflow.add_node("context_pre", context_engine.pre_process_sync)
        workflow.add_node("context_governor", context_engine.govern_context_sync)
        workflow.add_node("driver", driver)
        workflow.add_node("context_post", context_engine.post_process_sync)
        workflow.add_node("tools", QuadracodeTools)
        workflow.add_node("context_tool", context_engine.handle_tool_response_sync)

        workflow.add_edge(START, "context_pre")
        workflow.add_edge("context_pre", "context_governor")
        workflow.add_edge("context_governor", "driver")
        workflow.add_conditional_edges(
            "driver",
            tools_condition,
            {"tools": "context_post", END: END},
        )
        workflow.add_edge("context_post", "tools")
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

    return workflow.compile(checkpointer=CHECKPOINTER)
