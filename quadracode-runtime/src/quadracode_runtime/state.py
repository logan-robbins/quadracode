from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages


class RuntimeState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
