from __future__ import annotations

import json
import shutil
import time
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.e2e.test_end_to_end import (  # type: ignore[import]
    AGENT_ID,
    SUPERVISOR_MAILBOX,
    get_last_stream_id,
    log_stream_snapshot,
    read_stream_after,
    require_prerequisites,
    run_compose,
    send_message_to_orchestrator,
    wait_for_container,
    wait_for_human_response,
    wait_for_redis,
)


def _extract_ai_contents(payload_raw: str) -> list[str]:
    payload = json.loads(payload_raw)
    messages = payload.get("messages", [])
    outputs: list[str] = []
    for entry in messages:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") != "ai":
            continue
        data = entry.get("data")
        if isinstance(data, dict):
            content = data.get("content")
            if isinstance(content, str):
                outputs.append(content.strip())
    return [text for text in outputs if text]


@pytest.mark.e2e
def test_runtime_checkpoint_survives_orchestrator_restart():
    require_prerequisites()

    if shutil.which("docker") is None:
        pytest.fail("Docker CLI must be installed and available on PATH for runtime memory test")

    run_compose(["down", "-v"], check=False)
    run_compose(
        [
            "up",
            "--build",
            "-d",
            "redis",
            "redis-mcp",
            "agent-registry",
            "orchestrator-runtime",
            "agent-runtime",
        ]
    )

    try:
        wait_for_container("redis")
        wait_for_container("redis-mcp")
        wait_for_container("agent-registry")
        wait_for_container("orchestrator-runtime")
        wait_for_container("agent-runtime")
        wait_for_redis()

        baseline_human = get_last_stream_id(SUPERVISOR_MAILBOX)
        metrics_baseline = get_last_stream_id("qc:context:metrics")
        send_message_to_orchestrator("Remember that my project code name is Orion.", reply_to=AGENT_ID)
        first_response = wait_for_human_response(baseline_human)
        first_payload = first_response.get("payload") or ""
        first_ai_contents = _extract_ai_contents(first_payload)
        assert first_ai_contents, "Initial orchestrator turn produced no AI content"

        # Restart orchestrator container to verify checkpoint restoration
        run_compose(["restart", "orchestrator-runtime"])
        wait_for_container("orchestrator-runtime")

        # Give container a moment to resume polling
        time.sleep(5)

        second_baseline = get_last_stream_id(SUPERVISOR_MAILBOX)
        send_message_to_orchestrator("What is my project code name?", reply_to=AGENT_ID)
        second_response = wait_for_human_response(second_baseline)
        second_payload = second_response.get("payload") or ""
        second_ai_contents = _extract_ai_contents(second_payload)
        assert second_ai_contents, "Follow-up orchestrator turn produced no AI content after restart"

        combined_lower = " ".join(second_ai_contents).lower()
        assert "orion" in combined_lower, "Orchestrator failed to recall project code name after restart"

        # Ensure context metrics continued after restart
        metrics_after = read_stream_after("qc:context:metrics", metrics_baseline, count=400)
        deemed = [entry for entry in metrics_after if entry[1].get("event") == "post_process"]
        assert deemed, "Context metrics did not resume after orchestrator restart"
        log_stream_snapshot(SUPERVISOR_MAILBOX)
    finally:
        run_compose(["logs", "orchestrator-runtime"], check=False)
        run_compose(["logs", "agent-runtime"], check=False)
        run_compose(["down", "-v"], check=False)
