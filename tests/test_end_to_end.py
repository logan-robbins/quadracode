from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]  # Go up 1 parent from tests/ to project root
COMPOSE_FILE = ROOT / "docker-compose.yml"
COMPOSE_CMD = ["docker", "compose", "-f", str(COMPOSE_FILE)]
AGENT_ID = "agent-runtime"
REQUIRED_ENV_VARS = [
    "ANTHROPIC_API_KEY",
]

SUPERVISOR_RECIPIENT = os.environ.get("QUADRACODE_SUPERVISOR_RECIPIENT", "human")
SUPERVISOR_MAILBOX = f"qc:mailbox/{SUPERVISOR_RECIPIENT}"


logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


def _compose_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    if extra:
        env.update(extra)
    return env


def run_compose(args: list[str], *, env: dict[str, str] | None = None, check: bool = True,
                capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        COMPOSE_CMD + args,
        cwd=ROOT,
        env=_compose_env(env),
        check=False,
        capture_output=capture_output,
        text=True,
    )
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, proc.args, output=proc.stdout, stderr=proc.stderr
        )
    return proc


def redis_cli(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run_compose(
        ["exec", "-T", "redis", "redis-cli", *args],
        check=check,
        capture_output=True,
    )


def get_last_stream_id(stream: str) -> str:
    proc = redis_cli("--json", "XREVRANGE", stream, "+", "-", "COUNT", "1", check=False)
    if proc.returncode != 0 or not proc.stdout.strip():
        return "0-0"
    try:
        rows = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return "0-0"
    if not rows:
        return "0-0"
    entry = rows[0]
    if isinstance(entry, list) and entry:
        entry_id = entry[0]
        if isinstance(entry_id, str):
            return entry_id
    return "0-0"


def stream_id_gt(candidate: str, baseline: str) -> bool:
    try:
        cand_ms, cand_seq = (int(part) for part in candidate.split("-", 1))
        base_ms, base_seq = (int(part) for part in baseline.split("-", 1))
        return (cand_ms, cand_seq) > (base_ms, base_seq)
    except ValueError:
        return candidate > baseline


def read_stream(stream: str, *, count: int = 20) -> list[tuple[str, dict[str, str]]]:
    proc = redis_cli("--json", "XRANGE", stream, "-", "+", "COUNT", str(count), check=False)
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    try:
        rows = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    entries: list[tuple[str, dict[str, str]]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) != 2:
            continue
        entry_id, field_pairs = row
        if not isinstance(entry_id, str) or not isinstance(field_pairs, list):
            continue
        fields: dict[str, str] = {}
        for i in range(0, len(field_pairs), 2):
            key = field_pairs[i]
            if i + 1 >= len(field_pairs):
                break
            value = field_pairs[i + 1]
            if isinstance(key, str) and isinstance(value, str):
                fields[key] = value
        entries.append((entry_id, fields))
    return entries


def read_stream_after(stream: str, baseline: str, *, count: int = 100) -> list[tuple[str, dict[str, str]]]:
    entries = read_stream(stream, count=count)
    return [entry for entry in entries if stream_id_gt(entry[0], baseline)]


def wait_for_context_metrics(baseline_id: str, *, timeout: int = 60) -> list[tuple[str, dict[str, str]]]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        entries = read_stream_after("qc:context:metrics", baseline_id, count=200)
        if entries:
            return entries
        time.sleep(1)
    return []


def wait_for_container(service: str, *, timeout: int = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        proc = run_compose(["ps", "--format", "json", service], capture_output=True, check=False)
        output = (proc.stdout or "").strip()
        records: list[dict[str, str]] = []
        if output:
            try:
                parsed = json.loads(output)
            except json.JSONDecodeError:
                parsed = []
                for line in output.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(entry, dict):
                        parsed.append(entry)
            if isinstance(parsed, dict):
                records = [parsed]
            elif isinstance(parsed, list):
                records = [item for item in parsed if isinstance(item, dict)]
        if records and records[0].get("State") == "running":
            return
        time.sleep(2)
    raise TimeoutError(f"Service {service} did not reach running state within timeout")


def wait_for_redis(timeout: int = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        proc = redis_cli("PING", check=False)
        if proc.returncode == 0 and proc.stdout.strip() == "PONG":
            return
        time.sleep(1)
    raise TimeoutError("Redis did not respond to PING within timeout")


def _fetch_registry_json(path: str) -> dict:
    url = f"http://127.0.0.1:8090{path}"
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
        return json.load(response)


def wait_for_registry_agent(agent_id: str, *, timeout: int = 60) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            payload = _fetch_registry_json("/agents")
        except (urllib.error.URLError, Exception):
            payload = None
        if payload and isinstance(payload.get("agents"), list):
            for agent in payload["agents"]:
                if agent.get("agent_id") == agent_id:
                    return agent
        time.sleep(2)
    raise TimeoutError(f"Registry did not report agent {agent_id!r} within timeout")


def get_registry_stats() -> dict:
    return _fetch_registry_json("/stats")


def stream_info(stream: str) -> dict[str, str] | None:
    proc = redis_cli("--raw", "XINFO", "STREAM", stream, check=False)
    if proc.returncode != 0:
        return None
    tokens = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    info: dict[str, str] = {}
    it = iter(tokens)
    for key in it:
        try:
            value = next(it)
        except StopIteration:
            break
        info[key] = value
    return info


def stream_last_generated_id(stream: str) -> str | None:
    info = stream_info(stream)
    if not info:
        return None
    return info.get("last-generated-id")


def stream_entries_added(stream: str) -> int | None:
    info = stream_info(stream)
    if not info:
        return None
    raw_value = info.get("entries-added") or info.get("entries_added")
    if raw_value is None:
        return None
    try:
        return int(raw_value)
    except ValueError:
        return None


def _coerce_content_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text_val = item.get("text")
                if isinstance(text_val, str):
                    parts.append(text_val)
                else:
                    inner = item.get("content")
                    if isinstance(inner, str):
                        parts.append(inner)
        return "".join(parts).strip()
    if content is None:
        return ""
    return str(content).strip()


def log_stream_snapshot(stream: str, *, limit: int = 5) -> None:
    entries = read_stream(stream, count=limit)
    summary = [
        {
            "id": entry_id,
            "sender": fields.get("sender"),
            "recipient": fields.get("recipient"),
            "message": fields.get("message"),
        }
        for entry_id, fields in entries
    ]
    logger.info("Stream %s recent entries (limit=%d): %s", stream, limit, summary)


def send_message_to_orchestrator(message: str, reply_to: str | None = None) -> None:
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload_obj: dict[str, str] = {"supervisor": SUPERVISOR_RECIPIENT}
    if reply_to:
        payload_obj["reply_to"] = reply_to
    payload = json.dumps(payload_obj, separators=(",", ":"))
    redis_cli(
        "XADD",
        "qc:mailbox/orchestrator",
        "*",
        "timestamp",
        timestamp,
        "sender",
        SUPERVISOR_RECIPIENT,
        "recipient",
        "orchestrator",
        "message",
        message,
        "payload",
        payload,
    )


def wait_for_human_response(baseline_id: str, *, timeout: int = 120) -> dict[str, str]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for entry_id, fields in read_stream(SUPERVISOR_MAILBOX):
            if stream_id_gt(entry_id, baseline_id):
                return fields
        time.sleep(2)
    raise TimeoutError("Timed out waiting for response on human mailbox")

def require_prerequisites() -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI must be installed and available on PATH for end-to-end tests")
    missing = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]
    if missing:
        pytest.skip(
            "Missing required environment variables for real LLM calls: "
            + ", ".join(missing)
        )


@pytest.mark.e2e
def test_orchestrator_round_trip():
    require_prerequisites()

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
        agent_record = wait_for_registry_agent(AGENT_ID)
        stats_payload = get_registry_stats()
        assert agent_record.get("status") == "healthy"
        assert agent_record.get("agent_id") == AGENT_ID
        assert stats_payload.get("total_agents", 0) >= 1
        assert stats_payload.get("healthy_agents", 0) >= 1
        redis_cli("FLUSHALL")

        baseline_id = get_last_stream_id(SUPERVISOR_MAILBOX)
        agent_stream = f"qc:mailbox/{AGENT_ID}"
        baseline_agent_last_id = stream_last_generated_id(agent_stream)
        baseline_agent_entries = stream_entries_added(agent_stream) or 0
        baseline_orchestrator_entries = stream_entries_added("qc:mailbox/orchestrator") or 0
        metrics_baseline_id = get_last_stream_id("qc:context:metrics")
        logger.info(
            "Baselines: human_id=%s agent_last=%s agent_entries=%d orchestrator_entries=%d",
            baseline_id,
            baseline_agent_last_id,
            baseline_agent_entries,
            baseline_orchestrator_entries,
        )
        log_stream_snapshot(SUPERVISOR_MAILBOX)
        log_stream_snapshot(agent_stream)
        log_stream_snapshot("qc:mailbox/orchestrator")

        send_message_to_orchestrator("Hello from E2E test", reply_to=AGENT_ID)
        response_fields = wait_for_human_response(baseline_id)
        logger.info("Human received fields: sender=%s recipient=%s", response_fields.get("sender"), response_fields.get("recipient"))

        assert response_fields.get("sender") == "orchestrator"
        assert response_fields.get("recipient") == SUPERVISOR_RECIPIENT

        response_message = response_fields.get("message")
        assert response_message is not None, "orchestrator returned empty response"
        logger.info("Human-visible response text: %s", response_message)

        payload_raw = response_fields.get("payload")
        assert payload_raw, "orchestrator response missing payload"
        payload = json.loads(payload_raw)
        messages = payload.get("messages")
        assert isinstance(messages, list) and messages, "response payload missing message trace"
        ai_contents = [
            _coerce_content_text(entry.get("data", {}).get("content"))
            for entry in messages
            if isinstance(entry, dict) and entry.get("type") == "ai"
        ]
        ai_contents = [text for text in ai_contents if text]
        assert ai_contents, "AI responses lacked textual content"
        logger.info("AI content candidates: %s", ai_contents)

        agent_last_id = stream_last_generated_id(agent_stream)
        assert agent_last_id is not None, "agent mailbox never created"
        assert agent_last_id not in {"0-0", "0"}, "agent stream never advanced"
        if baseline_agent_last_id is not None:
            assert stream_id_gt(agent_last_id, baseline_agent_last_id), (
                "No traffic routed to agent mailbox"
            )

        agent_entries_after = stream_entries_added(agent_stream) or 0
        assert agent_entries_after > baseline_agent_entries, "agent stream did not record new entries"
        logger.info(
            "Agent stream stats: last_id=%s baseline_entries=%d after_entries=%d",
            agent_last_id,
            baseline_agent_entries,
            agent_entries_after,
        )

        orchestrator_entries_after = stream_entries_added("qc:mailbox/orchestrator") or 0
        assert orchestrator_entries_after >= baseline_orchestrator_entries + 2, (
            "orchestrator stream did not capture round-trip traffic"
        )
        logger.info(
            "Orchestrator stream baseline=%d after=%d",
            baseline_orchestrator_entries,
            orchestrator_entries_after,
        )
        log_stream_snapshot(agent_stream)
        log_stream_snapshot("qc:mailbox/orchestrator")
        log_stream_snapshot(SUPERVISOR_MAILBOX)

        metrics_entries = wait_for_context_metrics(metrics_baseline_id)
        context_snapshots: list[dict[str, object]] = []
        for entry_id, fields in metrics_entries:
            payload_raw = fields.get("payload", "{}")
            try:
                payload = json.loads(payload_raw)
            except json.JSONDecodeError:
                payload = {"raw": payload_raw}
            context_snapshots.append(
                {
                    "id": entry_id,
                    "event": fields.get("event"),
                    "payload": payload,
                }
            )

        assert context_snapshots, "Context metrics stream did not record activity"
        logger.info("Context engineering snapshots: %s", json.dumps(context_snapshots, indent=2))
        events_observed = {snapshot.get("event") for snapshot in context_snapshots}
        assert "pre_process" in events_observed
        # post_process is emitted on the tools path; the first turn may not invoke tools
        # curation is conditional on low quality or overflow and may not always occur
        assert "load" in events_observed
        assert "governor_plan" in events_observed

        load_events = [snapshot for snapshot in context_snapshots if snapshot.get("event") == "load"]
        assert load_events, "Load metrics were not emitted"
        assert any(
            snapshot.get("payload", {}).get("segments") for snapshot in load_events
        ), "Load metrics lacked segment details"

        # Ask orchestrator to report on registry state and confirm tool usage.
        second_baseline = get_last_stream_id(SUPERVISOR_MAILBOX)
        send_message_to_orchestrator(
            "How many agents do you have and what is their status?",
        )
        registry_response_fields = wait_for_human_response(second_baseline)
        registry_payload_raw = registry_response_fields.get("payload")
        assert registry_payload_raw, "orchestrator registry response missing payload"
        registry_payload = json.loads(registry_payload_raw)
        registry_messages = registry_payload.get("messages", [])
        logger.debug("Registry response messages: %s", registry_messages)
        tool_invocations = [
            entry
            for entry in registry_messages
            if isinstance(entry, dict)
            and entry.get("type") == "tool"
            and isinstance(entry.get("data"), dict)
            and entry["data"].get("name") == "agent_registry"
        ]
        assert tool_invocations, "orchestrator did not invoke agent_registry tool"
        tool_outputs = [
            _coerce_content_text(
                entry.get("data", {}).get("output")
                or entry.get("data", {}).get("content")
            )
            for entry in tool_invocations
        ]
        tool_outputs = [text for text in tool_outputs if text]
        assert any(AGENT_ID in text for text in tool_outputs), (
            "agent_registry tool output did not mention running agent"
        )
        ai_contents = [
            _coerce_content_text(entry.get("data", {}).get("content"))
            for entry in registry_messages
            if isinstance(entry, dict) and entry.get("type") == "ai"
        ]
        ai_contents = [text for text in ai_contents if text]
        assert ai_contents, "registry response lacked AI content"
        assert any(AGENT_ID in text for text in ai_contents), (
            "orchestrator response did not reference agent status"
        )

        # After tool invocation, ensure post_process metrics were emitted
        metrics_entries_after_tool = read_stream_after("qc:context:metrics", metrics_baseline_id, count=400)
        context_snapshots_after_tool: list[dict[str, object]] = []
        for entry_id, fields in metrics_entries_after_tool:
            payload_raw = fields.get("payload", "{}")
            try:
                payload2 = json.loads(payload_raw)
            except json.JSONDecodeError:
                payload2 = {"raw": payload_raw}
            context_snapshots_after_tool.append(
                {
                    "id": entry_id,
                    "event": fields.get("event"),
                    "payload": payload2,
                }
            )
        events_after_tool = {snapshot.get("event") for snapshot in context_snapshots_after_tool}
        assert "post_process" in events_after_tool, "Post-process metrics were not emitted after tool call"
    finally:
        run_compose(["logs", "orchestrator-runtime"], check=False)
        run_compose(["logs", "agent-runtime"], check=False)
        run_compose(["down", "-v"], check=False)


@pytest.mark.e2e
def test_context_engine_tunable_thresholds():
    require_prerequisites()

    # Lower thresholds to force curation/externalization and tool reduction
    extra_env = {
        "QUADRACODE_MAX_TOOL_PAYLOAD_CHARS": "10",
        "QUADRACODE_TARGET_CONTEXT_SIZE": "10",
        "QUADRACODE_REDUCER_MODEL": "heuristic",
        "QUADRACODE_GOVERNOR_MODEL": "heuristic",
    }

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
        ],
        env=extra_env,
    )

    try:
        wait_for_container("redis")
        wait_for_container("redis-mcp")
        wait_for_container("agent-registry")
        wait_for_container("orchestrator-runtime")
        wait_for_container("agent-runtime")
        wait_for_redis()

        # Ensure clean metrics and mailboxes
        redis_cli("FLUSHALL")

        metrics_baseline_id = get_last_stream_id("qc:context:metrics")

        # Use a message containing the keyword "test" to trigger progressive loader
        send_message_to_orchestrator("Hello from E2E test", reply_to=AGENT_ID)

        # Wait for initial context metrics (pre-process + load). Curation occurs on the next turn.
        metrics_entries = wait_for_context_metrics(metrics_baseline_id)
        assert metrics_entries, "No context metrics observed with lowered thresholds"
        snapshots: list[dict[str, object]] = []
        for entry_id, fields in metrics_entries:
            payload_raw = fields.get("payload", "{}")
            try:
                payload = json.loads(payload_raw)
            except json.JSONDecodeError:
                payload = {"raw": payload_raw}
            snapshots.append(
                {
                    "id": entry_id,
                    "event": fields.get("event"),
                    "payload": payload,
                }
            )

        events = {s.get("event") for s in snapshots}
        assert "load" in events and "pre_process" in events

        # Now encourage a tool call; the second turn's pre_process should trigger curation/externalize
        second_baseline = get_last_stream_id(SUPERVISOR_MAILBOX)
        metrics_baseline_second = get_last_stream_id("qc:context:metrics")
        send_message_to_orchestrator(
            "How many agents are registered? Provide details using the agent_registry tool.",
        )
        _ = wait_for_human_response(second_baseline)

        # With MAX_TOOL_PAYLOAD_CHARS=10 any tool output should trigger reduction+tool_response
        metrics_after_tool = read_stream_after("qc:context:metrics", metrics_baseline_second, count=400)
        tool_events = {fields.get("event") for _, fields in metrics_after_tool}
        assert "tool_response" in tool_events, "Expected tool_response metrics after tool call"

    finally:
        run_compose(["logs", "orchestrator-runtime"], check=False)
        run_compose(["logs", "agent-runtime"], check=False)
        run_compose(["down", "-v"], check=False)
