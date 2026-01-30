"""Pytest configuration for quadracode-tools tests.

This conftest overrides the root-level fixture that depends on quadracode_runtime,
allowing the tools package tests to run independently.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def stub_langchain_chat_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """No-op override of the root conftest's LLM stubbing fixture.

    The quadracode-tools package does not depend on quadracode_runtime,
    so we provide a no-op fixture to allow tests to run independently.
    """
    pass
