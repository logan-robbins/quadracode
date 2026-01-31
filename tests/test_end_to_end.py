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
# Agent IDs are now ephemeral (agent-{uuid}), discovered dynamically from registry
AGENT_ID_PREFIX = "agent-"
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
    # Check if we're running inside a container
    if os.environ.get("TEST_MODE") or os.path.exists("/.dockerenv"):
        # Running inside container - use redis-cli directly connecting to host
        host = os.environ.get("REDIS_HOST", "localhost")
        port = os.environ.get("REDIS_PORT", "6379")
        cmd = ["redis-cli", "-h", host, "-p", port, *args]
        # Install redis-cli if not available (won't be in Python container)
        # Instead, we'll use Python Redis client in wait_for_redis
        import redis as redis_module
        client = redis_module.Redis(host=host, port=int(port), decode_responses=True)
        try:
            # Simulate subprocess.CompletedProcess for compatibility
            if args[0] == "PING":
                result = client.ping()
                stdout = "PONG" if result else ""
            elif args[0] == "FLUSHALL":
                client.flushall()
                stdout = "OK"
            elif args[0] == "XADD":
                # Handle XADD command for sending messages
                # XADD stream_key * field1 value1 field2 value2 ...
                stream_key = args[1]
                # Skip the "*" (auto ID) at args[2]
                # Remaining args are field-value pairs
                fields = {}
                for i in range(3, len(args), 2):
                    if i+1 < len(args):
                        fields[args[i]] = args[i+1]
                result = client.xadd(stream_key, fields)
                stdout = result.decode() if isinstance(result, bytes) else str(result)
            elif args[0] == "XREVRANGE":
                # Handle XREVRANGE for reading streams
                stream_key = args[1]
                start = args[2]  # Usually "+"
                end = args[3]    # Usually "-"
                count = None
                if "COUNT" in args:
                    count_idx = args.index("COUNT")
                    if count_idx + 1 < len(args):
                        count = int(args[count_idx + 1])
                entries = client.xrevrange(stream_key, start, end, count)
                # Format as JSON if --json flag is present
                if "--json" in args:
                    stdout = json.dumps(entries) if entries else "[]"
                else:
                    stdout = str(entries)
            elif args[0] == "XRANGE":
                # Handle XRANGE for reading streams
                stream_key = args[1]
                start = args[2]  # Usually "-"
                end = args[3]    # Usually "+"
                count = None
                if "COUNT" in args:
                    count_idx = args.index("COUNT")
                    if count_idx + 1 < len(args):
                        count = int(args[count_idx + 1])
                entries = client.xrange(stream_key, start, end, count)
                # Format as JSON if --json flag is present
                if "--json" in args:
                    # Convert entries to JSON format expected by the test
                    json_entries = []
                    for entry_id, fields in entries:
                        json_entries.append([entry_id, fields])
                    stdout = json.dumps(json_entries) if json_entries else "[]"
                else:
                    stdout = str(entries)
            else:
                # For other commands, try to execute
                result = client.execute_command(*args)
                stdout = str(result) if result else ""
            
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=stdout,
                stderr=""
            )
        except Exception as e:
            if check:
                raise
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="",
                stderr=str(e)
            )
    else:
        # Original logic for running on host
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
    # Check if we're running inside a container
    if os.environ.get("TEST_MODE") or os.path.exists("/.dockerenv"):
        # Running inside container - connect directly to Redis
        import redis as redis_module
        host = os.environ.get("REDIS_HOST", "localhost")
        port = int(os.environ.get("REDIS_PORT", "6379"))
        
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                client = redis_module.Redis(host=host, port=port, decode_responses=True)
                if client.ping():
                    client.close()
                    return
            except Exception:
                pass
            time.sleep(1)
        raise TimeoutError("Redis did not respond to PING within timeout")
    else:
        # Original logic for running on host
        deadline = time.time() + timeout
        while time.time() < deadline:
            proc = redis_cli("PING", check=False)
            if proc.returncode == 0 and proc.stdout.strip() == "PONG":
                return
            time.sleep(1)
        raise TimeoutError("Redis did not respond to PING within timeout")


def _fetch_registry_json(path: str) -> dict:
    # Use environment-aware URL
    base_url = os.environ.get("AGENT_REGISTRY_URL", "http://localhost:8090")
    url = f"{base_url}{path}"
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
        return json.load(response)


def wait_for_registry_agent(agent_id_or_prefix: str, *, timeout: int = 60) -> dict:
    """Wait for an agent to register.

    Args:
        agent_id_or_prefix: Either an exact agent ID or a prefix to match.
            If the value ends with '-', it's treated as a prefix (e.g., 'agent-').
            Otherwise, it's treated as an exact match.
        timeout: Maximum time to wait in seconds.

    Returns:
        The agent record dict from the registry.

    Raises:
        TimeoutError: If no matching agent is found within timeout.
    """
    is_prefix = agent_id_or_prefix.endswith("-")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            payload = _fetch_registry_json("/agents")
        except (urllib.error.URLError, Exception):
            payload = None
        if payload and isinstance(payload.get("agents"), list):
            for agent in payload["agents"]:
                aid = agent.get("agent_id", "")
                if is_prefix:
                    if aid.startswith(agent_id_or_prefix):
                        return agent
                else:
                    if aid == agent_id_or_prefix:
                        return agent
        time.sleep(2)
    raise TimeoutError(f"Registry did not report agent matching {agent_id_or_prefix!r} within timeout")


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
    # Check if we're running inside a container
    # When running in the test container, we don't need docker CLI
    if os.environ.get("TEST_MODE") or os.path.exists("/.dockerenv"):
        # Running inside container - only check for API keys
        missing = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]
        if missing:
            pytest.skip(
                "Missing required environment variables for real LLM calls: "
                + ", ".join(missing)
            )
    else:
        # Running on host - check for docker CLI
        if shutil.which("docker") is None:
            pytest.skip("Docker CLI must be installed and available on PATH for end-to-end tests")
        missing = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]
        if missing:
            pytest.skip(
                "Missing required environment variables for real LLM calls: "
                + ", ".join(missing)
            )


# NOTE: The actual E2E tests have been migrated to tests/e2e_advanced/.
# This file now contains only shared utility functions used by the advanced test suite.
# See tests/e2e_advanced/test_foundation_smoke.py for quick smoke tests
# and tests/e2e_advanced/ for the comprehensive test suite.
