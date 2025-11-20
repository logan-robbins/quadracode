"""Pytest configuration for shared fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

import pytest
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage


def _load_env_files(paths: Iterable[Path]) -> None:
    """Load local dotenv files without overriding any pre-set environment vars."""
    for env_path in paths:
        if env_path.exists():
            load_dotenv(env_path, override=False)


_REPO_ROOT = Path(__file__).resolve().parent.parent
_load_env_files((_REPO_ROOT / ".env", _REPO_ROOT / ".env.docker"))


class _StubChatModel:
    """Minimal chat model stub that mirrors LangChain's invoke contract."""

    def __init__(self, model_name: str):
        self.model_name = model_name

    def invoke(self, messages: Sequence[BaseMessage], *_, **__) -> AIMessage:
        text = ""
        for message in reversed(messages):
            content = message.content
            if isinstance(content, list):
                content = " ".join(str(part) for part in content)
            else:
                content = str(content)
            text = content
            if isinstance(message, HumanMessage):
                break
        tokens = text.split()
        if len(tokens) > 25:
            text = " ".join(tokens[:25])
        return AIMessage(content=f"[stub:{self.model_name}] {text.strip()}")


@pytest.fixture(autouse=True)
def stub_langchain_chat_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Prevent external LLM calls during tests by stubbing LangChain chat model factory.
    """

    def _factory(model_name: str, *_, **__) -> _StubChatModel:
        return _StubChatModel(model_name)

    monkeypatch.setattr(
        "quadracode_runtime.nodes.context_reducer.init_chat_model",
        _factory,
        raising=True,
    )
    monkeypatch.setattr(
        "quadracode_runtime.nodes.context_engine.init_chat_model",
        _factory,
        raising=True,
    )
    monkeypatch.setattr(
        "quadracode_runtime.nodes.driver.init_chat_model",
        _factory,
        raising=True,
    )
