from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import pytest
from streamlit.testing.v1 import AppTest


def wait_for_redis(timeout: int = 60) -> None:
    deadline = time.time() + timeout
    import redis

    r = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)
    while time.time() < deadline:
        try:
            if r.ping():
                return
        except Exception:
            pass
        time.sleep(1)
    raise TimeoutError("Redis did not respond to PING within timeout")


def wait_for_registry(timeout: int = 60) -> None:
    url = "http://127.0.0.1:8090/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:  # noqa: S310
                if resp.status == 200:
                    return
        except Exception:
            pass
        time.sleep(1)
    raise TimeoutError("Agent registry health endpoint did not respond within timeout")


@pytest.mark.e2e
def test_ui_round_trip_with_full_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    """Assumes docker compose has already started Redis, registry, orchestrator, and agent.

    This test does not start/stop containers. It waits for local services, then
    runs the UI in-process and verifies end-to-end behavior with a real LLM and
    runtimes behind the scenes.
    """
    # Precondition: services are already running locally
    try:
        wait_for_redis()
        wait_for_registry()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Required services not ready: {exc}")

    # Ensure the UI points to the local services
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6379")
    monkeypatch.setenv("AGENT_REGISTRY_URL", "http://localhost:8090")

    # Run the actual app in-process
    import quadracode_ui.app as app_module

    tester = AppTest.from_file(app_module.__file__, default_timeout=5.0)
    tester.run()

    # Send a prompt via the chat input
    assert tester.chat_input
    ci = tester.chat_input[0]
    ci.set_value("Hello from UI E2E")
    ci.run()

    # Poll the app until an assistant message appears (watcher will rerun)
    deadline = time.time() + 60
    saw_assistant = False
    last_seen_count = 0
    while time.time() < deadline:
        # Trigger a run to process any queued reruns
        tester.run()
        if tester.chat_message and len(tester.chat_message) != last_seen_count:
            last_seen_count = len(tester.chat_message)
            # Inspect the last message; human messages are from our input
            last = tester.chat_message[-1]
            if last.markdown and last.markdown[0].value != "Hello from UI E2E":
                saw_assistant = True
                break
        time.sleep(1)

    assert saw_assistant, "UI did not render assistant response within timeout"

    # Also verify Streams tab renders entries without error
    assert tester.tabs
    streams_tab = tester.tabs[1]  # Access the Streams tab (index 1)
    assert streams_tab.expander, "Expected stream entries to render under expanders"
