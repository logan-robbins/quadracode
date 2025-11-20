import asyncio

from quadracode_runtime.config.context_engine import ContextEngineConfig
from quadracode_runtime.nodes.progressive_loader import ProgressiveContextLoader
from quadracode_runtime.state import make_initial_context_engine_state


def test_progressive_loader_skill_activation(tmp_path) -> None:
    skills_root = tmp_path / "skills" / "debugging"
    skills_root.mkdir(parents=True)
    skill_file = skills_root / "SKILL.md"
    skill_file.write_text(
        """---
name: Debugging Playbook
description: Core workflow for addressing recurring backend errors.
tags: debug,errors
links:
  - reference.md
---
Step-by-step instructions:
1. Inspect logs
2. Capture stack traces
3. Formulate hypothesis
""",
        encoding="utf-8",
    )
    (skills_root / "reference.md").write_text("Detailed reference for debugging.", encoding="utf-8")

    config = ContextEngineConfig(
        project_root=str(tmp_path),
        documentation_paths=[],
        skills_paths=["skills"],
        metrics_enabled=False,
    )
    loader = ProgressiveContextLoader(config)

    state = make_initial_context_engine_state(context_window_max=config.context_window_max)

    class DummyMessage:
        def __init__(self, text: str) -> None:
            self.content = text

    state["messages"] = [DummyMessage("We saw repeated errors, need debugging steps")]  # triggers skill selection

    result = asyncio.run(loader.prepare_context(state))

    catalog = result["skills_catalog"]
    assert any(meta.get("name") == "Debugging Playbook" for meta in catalog)

    active_slugs = {meta.get("slug") for meta in result["active_skills_metadata"]}
    assert "debugging-playbook" in active_slugs

    skill_segments = [segment for segment in result["context_segments"] if segment["type"].startswith("skill:")]
    assert skill_segments, "Skill main content should be integrated"

    queue_types = {entry["type"] for entry in result["prefetch_queue"]}
    assert any(item.startswith("skill_link:debugging-playbook") for item in queue_types)
