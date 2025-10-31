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

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "docker-compose.yml"
COMPOSE_CMD = ["docker", "compose", "-f", str(COMPOSE_FILE)]
AGENT_ID = "agent-runtime"


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
    payload = json.dumps({}, separators=(",", ":"))
    if reply_to:
        payload = json.dumps({"reply_to": reply_to}, separators=(",", ":"))
    redis_cli(
        "XADD",
        "qc:mailbox/orchestrator",
        "*",
        "timestamp",
        timestamp,
        "sender",
        "human",
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
        for entry_id, fields in read_stream("qc:mailbox/human"):
            if stream_id_gt(entry_id, baseline_id):
                return fields
        time.sleep(2)
    raise TimeoutError("Timed out waiting for response on human mailbox")


@pytest.mark.e2e
def test_orchestrator_round_trip():
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for end-to-end test")

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

        baseline_id = get_last_stream_id("qc:mailbox/human")
        agent_stream = f"qc:mailbox/{AGENT_ID}"
        baseline_agent_last_id = stream_last_generated_id(agent_stream)
        baseline_agent_entries = stream_entries_added(agent_stream) or 0
        baseline_orchestrator_entries = stream_entries_added("qc:mailbox/orchestrator") or 0
        logger.info(
            "Baselines: human_id=%s agent_last=%s agent_entries=%d orchestrator_entries=%d",
            baseline_id,
            baseline_agent_last_id,
            baseline_agent_entries,
            baseline_orchestrator_entries,
        )
        log_stream_snapshot("qc:mailbox/human")
        log_stream_snapshot(agent_stream)
        log_stream_snapshot("qc:mailbox/orchestrator")

        send_message_to_orchestrator("Hello from E2E test", reply_to=AGENT_ID)
        response_fields = wait_for_human_response(baseline_id)
        logger.info("Human received fields: sender=%s recipient=%s", response_fields.get("sender"), response_fields.get("recipient"))

        assert response_fields.get("sender") == "orchestrator"
        assert response_fields.get("recipient") == "human"

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
        log_stream_snapshot("qc:mailbox/human")

        # Ask orchestrator to report on registry state and confirm tool usage.
        second_baseline = get_last_stream_id("qc:mailbox/human")
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
    finally:
        run_compose(["logs", "orchestrator-runtime"], check=False)
        run_compose(["logs", "agent-runtime"], check=False)
        run_compose(["down", "-v"], check=False)
