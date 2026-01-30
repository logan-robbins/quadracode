"""
This module provides the `make_driver` factory function, which is responsible for 
creating the core decision-making component (the "driver") of the LangGraph.

The driver is the node in the graph that is responsible for interpreting the 
current state and deciding on the next action, which is typically to call a tool 
or to respond to the user. This module supports three driver types:
- "heuristic": Simple rule-based driver for testing
- "mock": Uses mock responses (for QUADRACODE_MOCK_MODE)
- Default: LLM-based driver for production

The choice of driver is determined by the `QUADRACODE_DRIVER_MODEL` environment 
variable, allowing for flexible configuration of the runtime's core logic.
"""
from __future__ import annotations

import logging
import os

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, AnyMessage, SystemMessage, ToolMessage

from ..state import QuadraCodeState
from ..mock_mode import is_mock_mode, MockLLMResponse


LOGGER = logging.getLogger(__name__)


def _coerce_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text_val = item.get("text")
                if isinstance(text_val, str):
                    parts.append(text_val)
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
        return " ".join(parts).strip()
    if isinstance(content, dict):
        return " ".join(str(v) for v in content.values()).strip()
    return str(content).strip() if content is not None else ""


def make_driver(system_prompt: str, tools: list) -> callable:
    """
    Factory function that creates and returns the appropriate driver for the 
    LangGraph.

    This function reads the `QUADRACODE_DRIVER_MODEL` environment variable to 
    determine whether to create a simple heuristic-based driver or a more 
    sophisticated LLM-based driver. The driver is a key component of the graph, 
    responsible for making decisions based on the current state.

    Args:
        system_prompt: The base system prompt for the driver.
        tools: A list of tools that the driver can use.

    Returns:
        A callable that serves as the driver for the LangGraph.
    """
    driver_model = os.environ.get("QUADRACODE_DRIVER_MODEL", "").strip().lower()
    
    # Auto-select mock driver when mock mode is enabled
    if is_mock_mode() and driver_model not in ("heuristic", "mock"):
        driver_model = "mock"
        LOGGER.info("[MOCK] Auto-selected mock driver")
    
    if driver_model == "mock":
        def mock_driver(state: QuadraCodeState) -> dict[str, list[AnyMessage]]:
            """
            Mock driver that uses MockLLMResponse for predictable responses.
            
            Useful for standalone testing without LLM API calls.
            """
            msgs: list[AnyMessage] = state.get("messages", [])
            
            if not msgs:
                return {"messages": [AIMessage(content="[MOCK] Ready.")]}
            
            last_message = msgs[-1]
            
            # Handle tool responses
            if isinstance(last_message, ToolMessage):
                tool_name = getattr(last_message, "name", "tool")
                tool_output = _coerce_text(getattr(last_message, "content", ""))
                summary = tool_output[:200] if tool_output else "No output."
                reply = f"[MOCK] Tool '{tool_name}' result: {summary}"
                return {"messages": [AIMessage(content=reply)]}
            
            # Use MockLLMResponse for other messages
            ai_msg = MockLLMResponse.generate_response(msgs)
            return {"messages": [ai_msg]}
        
        LOGGER.info("[MOCK] Using mock driver")
        return mock_driver

    if driver_model == "heuristic":

        def heuristic_driver(state: RuntimeState) -> dict[str, list[AnyMessage]]:
            """
            A simple, heuristic-based driver for testing and development.

            This driver uses a set of simple rules to respond to messages and 
            call tools. It is not intended for production use but is useful for 
            testing the basic mechanics of the graph without incurring the cost 
            and latency of an LLM.
            """
            msgs: list[AnyMessage] = state.get("messages", [])
            if not msgs:
                return {"messages": [AIMessage(content="Acknowledged.")] }

            last_message = msgs[-1]

            if isinstance(last_message, ToolMessage):
                tool_name = getattr(last_message, "name", "tool")
                tool_output = _coerce_text(getattr(last_message, "content", ""))
                summary = tool_output or "No details returned."
                reply = f"{tool_name} report: {summary}"
                if "agent-runtime" not in reply:
                    reply += " Agent agent-runtime is healthy."
                return {"messages": [AIMessage(content=reply)]}

            last_text = _coerce_text(getattr(last_message, "content", ""))
            lower = last_text.lower()
            if any(keyword in lower for keyword in ("how many", "status", "agent")):
                ai_msg = AIMessage(
                    content="Checking agent registry for the latest agent counts.",
                    tool_calls=[
                        {
                            "name": "agent_registry",
                            "args": {"operation": "stats"},
                            "id": "call_registry_stats",
                        }
                    ],
                )
                return {"messages": [ai_msg]}

            ack = last_text or "Received your request."
            response = f"Agent agent-runtime will take care of this. Details: {ack}"
            return {"messages": [AIMessage(content=response)]}

        return heuristic_driver

    llm = init_chat_model("anthropic:claude-sonnet-4-20250514")

    def driver(state: QuadraCodeState) -> dict[str, list[AnyMessage]]:
        """
        An LLM-based driver that uses a language model to make decisions.

        This driver dynamically constructs a detailed system prompt by combining 
        the base prompt with contextual information from the state, such as the 
        governor's plan, active skills, and memory guidance. It then invokes the 
        LLM with this context to generate the next action.
        """
        msgs: list[AnyMessage] = state["messages"]
        outline = state.get("governor_prompt_outline", {}) if isinstance(state, dict) else {}
        system_sections = [system_prompt]

        outline_system = outline.get("system") if isinstance(outline, dict) else None
        if outline_system:
            system_sections.append(str(outline_system))

        outline_focus = outline.get("focus") if isinstance(outline, dict) else None
        if outline_focus:
            if isinstance(outline_focus, (list, tuple)):
                focus_block = "Focus:\n" + "\n".join(f"- {item}" for item in outline_focus)
            else:
                focus_block = f"Focus: {outline_focus}"
            system_sections.append(focus_block)

        outline_order = outline.get("ordered_segments") if isinstance(outline, dict) else None
        if outline_order:
            joined = ", ".join(str(item) for item in outline_order)
            system_sections.append(f"Suggested context order: {joined}")

        ledger_block = state.get("refinement_memory_block") if isinstance(state, dict) else None
        if ledger_block:
            system_sections.append(str(ledger_block))

        if isinstance(state, dict):
            skills_metadata = state.get("active_skills_metadata", [])
            deliberative_synopsis = state.get("deliberative_synopsis")
            deliberative_plan = state.get("deliberative_plan")
            memory_guidance = state.get("memory_guidance")
        else:
            skills_metadata = []
            deliberative_synopsis = None
            deliberative_plan = None
            memory_guidance = None

        if skills_metadata:
            skill_lines: list[str] = []
            for meta in skills_metadata[-6:]:
                name = str(meta.get("name") or meta.get("slug") or "skill")
                description = str(meta.get("description") or "")
                tags = meta.get("tags") or []
                tag_suffix = f" (tags: {', '.join(tags)})" if tags else ""
                if description:
                    skill_lines.append(f"- {name}{tag_suffix}: {description}")
                else:
                    skill_lines.append(f"- {name}{tag_suffix}")
            if skill_lines:
                system_sections.append("Available skills:\n" + "\n".join(skill_lines))

        if deliberative_synopsis:
            system_sections.append("Deliberative plan summary:\n" + str(deliberative_synopsis))

        if isinstance(deliberative_plan, dict):
            chain = deliberative_plan.get("reasoning_chain") or []
            if isinstance(chain, list) and chain:
                chain_lines: list[str] = []
                for item in chain[:5]:
                    if not isinstance(item, dict):
                        continue
                    step_id = item.get("step_id") or "step"
                    phase = item.get("phase") or "phase"
                    action = item.get("action") or "action"
                    outcome = item.get("expected_outcome") or "outcome"
                    confidence_value = item.get("confidence", 0.0)
                    try:
                        confidence = float(confidence_value)
                    except (TypeError, ValueError):
                        confidence = 0.0
                    chain_lines.append(
                        f"{step_id} [{phase}] {action} -> {outcome} (p={confidence:.2f})"
                    )
                if chain_lines:
                    system_sections.append("Reasoning chain:\n" + "\n".join(chain_lines))

        if isinstance(memory_guidance, dict) and memory_guidance:
            summary = memory_guidance.get("summary")
            recommendations = memory_guidance.get("recommendations") or []
            guidance_lines: list[str] = []
            if summary:
                guidance_lines.append(str(summary))
            for recommendation in recommendations[:3]:
                guidance_lines.append(f"- {recommendation}")
            support_cycles = memory_guidance.get("supporting_cycles") or []
            if support_cycles:
                guidance_lines.append(
                    "Supporting cycles: " + ", ".join(str(item) for item in support_cycles[:5])
                )
            system_sections.append("Memory guidance:\n" + "\n".join(guidance_lines))

        combined_system_prompt = "\n\n".join(section for section in system_sections if section)
        
        # Inject context segments into the conversation
        context_segments = state.get("context_segments", []) if isinstance(state, dict) else []
        LOGGER.info(f"DEBUG: Driver received state with {len(context_segments)} context_segments")
        for seg in context_segments:
            LOGGER.info(f"DEBUG: Segment: {seg.get('id')} - {seg.get('type')}")
        if context_segments:
            # Build context injection from segments marked in governor's ordered_segments
            ordered_segments = outline.get("ordered_segments", []) if isinstance(outline, dict) else []
            context_blocks = []

            LOGGER.info(f"Driver context injection: {len(context_segments)} total segments, {len(ordered_segments)} ordered")
            
            # First add segments that are in the ordered list
            for segment_id in ordered_segments:
                for segment in context_segments:
                    if segment.get("id") == segment_id:
                        content = segment.get("content", "")
                        seg_type = segment.get("type", "context")
                        if content:
                            context_blocks.append(f"[{seg_type}: {segment_id}]\n{content}")
                            LOGGER.info(f"  Added ordered segment: {segment_id} ({len(content)} chars)")
            
            # Add any high-priority segments not in ordered list (priority >= 8)
            for segment in context_segments:
                seg_id = segment.get("id")
                if seg_id not in ordered_segments and segment.get("priority", 0) >= 8:
                    content = segment.get("content", "")
                    seg_type = segment.get("type", "context")
                    if content:
                        context_blocks.append(f"[{seg_type}: {seg_id}]\n{content}")
                        LOGGER.info(f"  Added high-priority segment: {seg_id} ({len(content)} chars)")
            
            if context_blocks:
                # Add context as a system message right after the main system prompt
                context_injection = "# Active Context\n\n" + "\n\n".join(context_blocks)
                combined_system_prompt = combined_system_prompt + "\n\n" + context_injection
                LOGGER.info(f"  Context injection complete: {len(context_blocks)} blocks, {len(context_injection)} chars total")
            else:
                LOGGER.warning(f"  No context blocks generated despite having {len(context_segments)} segments and {len(ordered_segments)} ordered!")

        if not msgs or not isinstance(msgs[0], SystemMessage):
            msgs = [SystemMessage(content=combined_system_prompt), *msgs]
        else:
            msgs = [SystemMessage(content=combined_system_prompt), *msgs[1:]]

        # Debug: log a snippet of the system prompt to verify context injection
        if "# Active Context" in combined_system_prompt:
            LOGGER.info(f"✓ Active Context section present in system prompt ({len(combined_system_prompt)} chars total)")
        else:
            LOGGER.warning(f"✗ Active Context section NOT present in system prompt (segments={len(context_segments)}, ordered={len(ordered_segments) if isinstance(outline, dict) and 'ordered_segments' in outline else 0})")

        llm_with_tools = llm.bind_tools(tools)
        ai_msg = llm_with_tools.invoke(msgs)
        return {"messages": [ai_msg]}

    return driver
