from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import tools_condition

from .nodes.driver import make_driver
from .nodes.tool_node import QuadracodeTools
from .state import RuntimeState


CHECKPOINTER = MemorySaver()


def build_graph(system_prompt: str):
    driver = make_driver(system_prompt, QuadracodeTools.tools)

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
