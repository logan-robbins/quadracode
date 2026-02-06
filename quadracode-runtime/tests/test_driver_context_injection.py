"""Test that the driver properly injects context segments into the LLM prompt."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import HumanMessage, SystemMessage
from quadracode_runtime.nodes.driver import make_driver


@pytest.fixture
def anyio_backend():
    """Pin async tests to asyncio (trio is not installed)."""
    return "asyncio"


def _make_mock_llm() -> MagicMock:
    """Create a mock LLM whose ``bind_tools(...).ainvoke()`` returns a canned response."""
    mock_llm = MagicMock()
    bound = MagicMock()
    bound.ainvoke = AsyncMock(return_value=MagicMock(content="Response"))
    mock_llm.bind_tools.return_value = bound
    return mock_llm


@pytest.mark.anyio("asyncio")
async def test_driver_includes_context_segments():
    """Test that the driver includes context segments in the system prompt."""
    mock_llm = _make_mock_llm()

    with patch('quadracode_runtime.nodes.driver.init_chat_model', return_value=mock_llm):
        driver = make_driver("Base system prompt", tools=[])

    test_state = {
        "messages": [HumanMessage(content="Test question")],
        "context_segments": [
            {
                "id": "summary",
                "content": "Important context content",
                "type": "summary",
                "priority": 10
            }
        ],
        "governor_prompt_outline": {
            "ordered_segments": ["summary"]
        }
    }

    await driver(test_state)

    sent_messages = mock_llm.bind_tools.return_value.ainvoke.call_args[0][0]
    system_msg = sent_messages[0]

    assert isinstance(system_msg, SystemMessage)
    assert "Base system prompt" in system_msg.content
    assert "Important context content" in system_msg.content
    assert "[summary: summary]" in system_msg.content


@pytest.mark.anyio("asyncio")
async def test_driver_respects_governor_segment_order():
    """Test that the driver respects the governor's segment ordering."""
    mock_llm = _make_mock_llm()

    with patch('quadracode_runtime.nodes.driver.init_chat_model', return_value=mock_llm):
        driver = make_driver("Base", tools=[])

    test_state = {
        "messages": [HumanMessage(content="Test")],
        "context_segments": [
            {"id": "seg1", "content": "First", "type": "type1", "priority": 5},
            {"id": "seg2", "content": "Second", "type": "type2", "priority": 5},
            {"id": "seg3", "content": "Third", "type": "type3", "priority": 5},
        ],
        "governor_prompt_outline": {
            "ordered_segments": ["seg2", "seg1"]  # Different order
        }
    }

    await driver(test_state)
    sent_messages = mock_llm.bind_tools.return_value.ainvoke.call_args[0][0]
    system_content = sent_messages[0].content

    # seg2 should appear before seg1 in the output
    seg2_pos = system_content.find("Second")
    seg1_pos = system_content.find("First")
    assert seg2_pos < seg1_pos, "seg2 should come before seg1"

    # seg3 is not in ordered_segments and has low priority, so should not be included
    assert "Third" not in system_content


@pytest.mark.anyio("asyncio")
async def test_driver_includes_high_priority_segments():
    """Test that high priority segments are included even if not in governor's list."""
    mock_llm = _make_mock_llm()

    with patch('quadracode_runtime.nodes.driver.init_chat_model', return_value=mock_llm):
        driver = make_driver("Base", tools=[])

    test_state = {
        "messages": [HumanMessage(content="Test")],
        "context_segments": [
            {"id": "low", "content": "Low priority", "type": "info", "priority": 3},
            {"id": "high", "content": "High priority", "type": "critical", "priority": 9},
        ],
        "governor_prompt_outline": {
            "ordered_segments": []  # Governor doesn't specify any segments
        }
    }

    await driver(test_state)
    sent_messages = mock_llm.bind_tools.return_value.ainvoke.call_args[0][0]
    system_content = sent_messages[0].content

    # High priority (>=8) segment should be included
    assert "High priority" in system_content
    # Low priority segment should not be included
    assert "Low priority" not in system_content
