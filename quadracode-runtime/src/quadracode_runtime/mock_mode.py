"""
Mock mode support for standalone testing without Redis/LLM dependencies.

This module provides mock implementations for:
- Redis messaging (using fakeredis)
- LLM responses (using deterministic mock responses)
- MCP tools (returning predictable mock results)

Enable mock mode by setting QUADRACODE_MOCK_MODE=true environment variable.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from quadracode_contracts import MessageEnvelope

LOGGER = logging.getLogger(__name__)


def is_mock_mode() -> bool:
    """Check if mock mode is enabled via environment variable."""
    value = os.environ.get("QUADRACODE_MOCK_MODE", "").lower().strip()
    return value in ("true", "1", "yes", "on")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ============================================================================
# Mock Redis Tools
# ============================================================================

class MockRedisStorage:
    """In-memory storage for mock Redis streams."""
    
    _instance: "MockRedisStorage | None" = None
    
    def __init__(self) -> None:
        self._streams: Dict[str, List[Tuple[str, Dict[str, str]]]] = {}
        self._entry_counter = 0
    
    @classmethod
    def get_instance(cls) -> "MockRedisStorage":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def xadd(self, key: str, fields: Dict[str, str]) -> str:
        """Add entry to stream."""
        self._entry_counter += 1
        entry_id = f"{int(datetime.now(timezone.utc).timestamp() * 1000)}-{self._entry_counter}"
        if key not in self._streams:
            self._streams[key] = []
        self._streams[key].append((entry_id, fields))
        LOGGER.debug("[MOCK] xadd %s -> %s", key, entry_id)
        return entry_id
    
    def xrange(self, key: str, count: int = 10) -> List[Tuple[str, Dict[str, str]]]:
        """Read entries from stream."""
        entries = self._streams.get(key, [])[:count]
        LOGGER.debug("[MOCK] xrange %s count=%d -> %d entries", key, count, len(entries))
        return entries
    
    def xdel(self, key: str, entry_id: str) -> int:
        """Delete entry from stream."""
        if key not in self._streams:
            return 0
        original_len = len(self._streams[key])
        self._streams[key] = [
            (eid, fields) for eid, fields in self._streams[key] if eid != entry_id
        ]
        deleted = original_len - len(self._streams[key])
        LOGGER.debug("[MOCK] xdel %s %s -> %d deleted", key, entry_id, deleted)
        return deleted
    
    def clear(self) -> None:
        """Clear all streams."""
        self._streams.clear()
        self._entry_counter = 0


class XAddInput(BaseModel):
    key: str = Field(description="Stream key")
    fields: Dict[str, str] = Field(description="Fields to add")


class XRangeInput(BaseModel):
    key: str = Field(description="Stream key")
    count: int = Field(default=10, description="Max entries to return")


class XDelInput(BaseModel):
    key: str = Field(description="Stream key")
    entry_id: str = Field(description="Entry ID to delete")


def _mock_xadd(key: str, fields: Dict[str, str]) -> str:
    return MockRedisStorage.get_instance().xadd(key, fields)


def _mock_xrange(key: str, count: int = 10) -> str:
    entries = MockRedisStorage.get_instance().xrange(key, count)
    if not entries:
        return f"Stream '{key}' is empty or does not exist."
    return repr(entries)


def _mock_xdel(key: str, entry_id: str) -> str:
    deleted = MockRedisStorage.get_instance().xdel(key, entry_id)
    return str(deleted)


def get_mock_redis_tools() -> List[BaseTool]:
    """Get mock Redis tools that use in-memory storage."""
    return [
        StructuredTool.from_function(
            func=_mock_xadd,
            name="xadd",
            description="Add entry to Redis stream",
            args_schema=XAddInput,
        ),
        StructuredTool.from_function(
            func=_mock_xrange,
            name="xrange",
            description="Read entries from Redis stream",
            args_schema=XRangeInput,
        ),
        StructuredTool.from_function(
            func=_mock_xdel,
            name="xdel",
            description="Delete entry from Redis stream",
            args_schema=XDelInput,
        ),
    ]


# ============================================================================
# Mock LLM
# ============================================================================

class MockLLMResponse:
    """Generates deterministic mock LLM responses for testing."""
    
    _response_counter = 0
    
    @classmethod
    def generate_response(cls, messages: List[BaseMessage]) -> AIMessage:
        """Generate a mock response based on the input messages."""
        cls._response_counter += 1
        
        # Extract the last user message
        last_message = ""
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                content = msg.content
                last_message = content if isinstance(content, str) else str(content)
                break
        
        # Generate a predictable response
        response_text = (
            f"[MOCK RESPONSE #{cls._response_counter}] "
            f"Acknowledged: {last_message[:100]}..." if len(last_message) > 100 else last_message
        )
        
        return AIMessage(content=response_text)


# ============================================================================
# Mock Messaging
# ============================================================================

class MockRedisMCPMessaging:
    """Mock implementation of RedisMCPMessaging using in-memory storage."""
    
    def __init__(self) -> None:
        self._storage = MockRedisStorage.get_instance()
    
    @classmethod
    async def create(cls) -> "MockRedisMCPMessaging":
        """Create mock messaging instance (no async init needed)."""
        LOGGER.info("[MOCK] Created MockRedisMCPMessaging")
        return cls()
    
    async def publish(self, recipient: str, envelope: MessageEnvelope) -> str:
        """Publish a message to mock storage."""
        stream_key = f"qc:mailbox/{recipient}"
        entry_id = self._storage.xadd(stream_key, envelope.to_stream_fields())
        return entry_id
    
    async def read(
        self, recipient: str, *, batch_size: int = 10
    ) -> List[Tuple[str, MessageEnvelope]]:
        """Read messages from mock storage."""
        stream_key = f"qc:mailbox/{recipient}"
        raw_entries = self._storage.xrange(stream_key, batch_size)
        entries = []
        for entry_id, fields in raw_entries:
            envelope = MessageEnvelope.from_stream_fields(fields)
            entries.append((entry_id, envelope))
        return entries
    
    async def delete(self, recipient: str, entry_id: str) -> str:
        """Delete message from mock storage."""
        stream_key = f"qc:mailbox/{recipient}"
        deleted = self._storage.xdel(stream_key, entry_id)
        return str(deleted)


# ============================================================================
# Mock Mode Integration
# ============================================================================

def configure_mock_mode() -> None:
    """
    Configure the runtime for mock mode operation.
    
    This patches the necessary components to use mock implementations.
    Call this early in the application startup when mock mode is enabled.
    """
    if not is_mock_mode():
        return
    
    LOGGER.info("=== MOCK MODE ENABLED ===")
    LOGGER.info("Using mock implementations for Redis/LLM/MCP")
    
    # Set environment variables to disable features that require real services
    os.environ.setdefault("QUADRACODE_LOCAL_DEV_MODE", "1")
    os.environ.setdefault("QUADRACODE_DISABLE_REGISTRY", "1")


def get_mock_tools_if_enabled() -> List[BaseTool] | None:
    """
    Return mock tools if mock mode is enabled, otherwise None.
    
    This can be used to conditionally provide mock tools to the MCP loader.
    """
    if is_mock_mode():
        return get_mock_redis_tools()
    return None
