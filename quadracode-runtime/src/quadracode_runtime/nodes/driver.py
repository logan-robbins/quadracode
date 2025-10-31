from __future__ import annotations

from langchain.chat_models import init_chat_model
from langchain_core.messages import AnyMessage, SystemMessage

from ..state import RuntimeState


def make_driver(system_prompt: str, tools: list) -> callable:
    llm = init_chat_model("anthropic:claude-sonnet-4-20250514")

    def driver(state: RuntimeState) -> dict[str, list[AnyMessage]]:
        msgs: list[AnyMessage] = state["messages"]
        if not msgs or not isinstance(msgs[0], SystemMessage):
            msgs = [SystemMessage(content=system_prompt), *msgs]

        llm_with_tools = llm.bind_tools(tools)
        ai_msg = llm_with_tools.invoke(msgs)
        return {"messages": [ai_msg]}

    return driver
