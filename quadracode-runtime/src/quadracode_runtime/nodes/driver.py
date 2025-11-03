from __future__ import annotations

from langchain.chat_models import init_chat_model
from langchain_core.messages import AnyMessage, SystemMessage

from ..state import RuntimeState


def make_driver(system_prompt: str, tools: list) -> callable:
    llm = init_chat_model("anthropic:claude-sonnet-4-20250514")

    def driver(state: RuntimeState) -> dict[str, list[AnyMessage]]:
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

        if isinstance(state, dict):
            skills_metadata = state.get("active_skills_metadata", [])
        else:
            skills_metadata = []

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

        combined_system_prompt = "\n\n".join(section for section in system_sections if section)

        if not msgs or not isinstance(msgs[0], SystemMessage):
            msgs = [SystemMessage(content=combined_system_prompt), *msgs]
        else:
            msgs = [SystemMessage(content=combined_system_prompt), *msgs[1:]]

        llm_with_tools = llm.bind_tools(tools)
        ai_msg = llm_with_tools.invoke(msgs)
        return {"messages": [ai_msg]}

    return driver
