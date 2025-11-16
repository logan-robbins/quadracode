"""Artifact capture utilities for advanced E2E tests.

This module provides functions to capture Docker logs, workspace state,
PRP refinement ledgers, and time-travel debugging logs.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

# Import base compose utilities
import sys
parent_tests = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(parent_tests))
from test_end_to_end import COMPOSE_CMD, ROOT, run_compose


def capture_docker_logs(service: str, output_path: Path) -> None:
    """Capture Docker Compose service logs to a file.

    Args:
        service: Name of the service (e.g., "orchestrator-runtime", "agent-runtime")
        output_path: Path to write logs

    Example:
        >>> capture_docker_logs(
        ...     service="orchestrator-runtime",
        ...     output_path=Path("logs/test_123/orchestrator.log"),
        ... )
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    proc = run_compose(
        ["logs", "--no-color", "--timestamps", service],
        capture_output=True,
        check=False,
    )

    with output_path.open("w") as f:
        f.write(proc.stdout)
        if proc.stderr:
            f.write("\n\n=== STDERR ===\n\n")
            f.write(proc.stderr)


def capture_all_service_logs(output_dir: Path, services: list[str] | None = None) -> None:
    """Capture logs for all specified services.

    Args:
        output_dir: Directory to write log files
        services: List of service names, or None for all services

    Example:
        >>> capture_all_service_logs(
        ...     output_dir=Path("logs/test_123"),
        ...     services=["redis", "orchestrator-runtime", "agent-runtime"],
        ... )
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if services is None:
        # Get all running services
        proc = run_compose(["ps", "--format", "json"], capture_output=True, check=False)
        if proc.returncode == 0 and proc.stdout.strip():
            try:
                parsed = json.loads(proc.stdout)
                if isinstance(parsed, dict):
                    services = [parsed.get("Service", "")]
                elif isinstance(parsed, list):
                    services = []
                    for line in proc.stdout.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            if isinstance(entry, dict):
                                service_name = entry.get("Service")
                                if service_name:
                                    services.append(service_name)
                        except json.JSONDecodeError:
                            continue
            except json.JSONDecodeError:
                services = []

    if not services:
        return

    for service in services:
        if not service:
            continue
        safe_name = service.replace("/", "_").replace(":", "_")
        output_path = output_dir / f"{safe_name}.log"
        capture_docker_logs(service, output_path)


def capture_workspace_state(workspace_id: str, output_dir: Path) -> None:
    """Capture the current state of a workspace filesystem.

    Note: This is a placeholder. The actual implementation would need to use
    the workspace_copy_from tool or docker cp commands.

    Args:
        workspace_id: Workspace identifier
        output_dir: Directory to write workspace files

    Example:
        >>> capture_workspace_state(
        ...     workspace_id="ws-test-123",
        ...     output_dir=Path("artifacts/test_123/workspace"),
        ... )
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Try to use docker cp to copy workspace contents
    # Workspace containers are typically named like: quadracode-workspace-{workspace_id}
    container_name = f"quadracode-workspace-{workspace_id}"

    proc = subprocess.run(
        ["docker", "ps", "--filter", f"name={container_name}", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )

    if proc.returncode != 0 or not proc.stdout.strip():
        # Workspace container not found, create a marker file
        marker_file = output_dir / "workspace_not_found.txt"
        with marker_file.open("w") as f:
            f.write(f"Workspace container '{container_name}' not found\n")
        return

    actual_container_name = proc.stdout.strip()

    # Copy workspace contents
    subprocess.run(
        ["docker", "cp", f"{actual_container_name}:/workspace", str(output_dir)],
        check=False,
        capture_output=True,
    )


def capture_prp_ledger(state: dict[str, Any], output_path: Path) -> None:
    """Extract and save the PRP refinement ledger from orchestrator state.

    Args:
        state: Orchestrator state dictionary (from time-travel log or state dump)
        output_path: Path to write ledger JSON

    Example:
        >>> state = {...}  # From time-travel log
        >>> capture_prp_ledger(
        ...     state=state,
        ...     output_path=Path("artifacts/test_123/refinement_ledger.json"),
        ... )
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Extract refinement_ledger from state
    ledger = state.get("refinement_ledger", [])

    ledger_data = {
        "timestamp": state.get("timestamp"),
        "prp_state": state.get("prp_state"),
        "prp_cycle_count": state.get("prp_cycle_count"),
        "ledger_entries": ledger,
        "ledger_size": len(ledger),
    }

    with output_path.open("w") as f:
        json.dump(ledger_data, f, indent=2, default=str)


def capture_time_travel_logs(
    service: str, output_dir: Path, shared_volume_path: str = "/shared/time_travel_logs"
) -> None:
    """Copy time-travel JSONL logs from a service container.

    Args:
        service: Service name (e.g., "orchestrator-runtime")
        output_dir: Directory to write logs
        shared_volume_path: Path to time-travel logs inside container

    Example:
        >>> capture_time_travel_logs(
        ...     service="orchestrator-runtime",
        ...     output_dir=Path("artifacts/test_123/time_travel"),
        ... )
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get container name for the service
    proc = run_compose(
        ["ps", "--format", "json", service],
        capture_output=True,
        check=False,
    )

    if proc.returncode != 0 or not proc.stdout.strip():
        return

    try:
        parsed = json.loads(proc.stdout)
        if isinstance(parsed, list) and parsed:
            container_name = parsed[0].get("Name")
        elif isinstance(parsed, dict):
            container_name = parsed.get("Name")
        else:
            container_name = None
    except json.JSONDecodeError:
        container_name = None

    if not container_name:
        return

    # Copy time-travel logs from container
    subprocess.run(
        ["docker", "cp", f"{container_name}:{shared_volume_path}", str(output_dir)],
        check=False,
        capture_output=True,
    )


def capture_context_metrics(output_path: Path, entries: list[tuple[str, dict[str, str]]]) -> None:
    """Save context engineering metrics to a structured JSON file.

    Args:
        output_path: Path to write metrics JSON
        entries: List of (entry_id, fields) tuples from qc:context:metrics stream

    Example:
        >>> entries = read_stream(redis_client, "qc:context:metrics", count=1000)
        >>> capture_context_metrics(
        ...     output_path=Path("artifacts/test_123/context_metrics.json"),
        ...     entries=entries,
        ... )
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Parse and structure metrics
    metrics_by_event: dict[str, list[dict[str, Any]]] = {}

    for entry_id, fields in entries:
        event_type = fields.get("event", "unknown")
        payload_raw = fields.get("payload", "{}")

        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            payload = {"raw": payload_raw}

        event_data = {
            "stream_id": entry_id,
            "timestamp": fields.get("timestamp"),
            "payload": payload,
        }

        if event_type not in metrics_by_event:
            metrics_by_event[event_type] = []
        metrics_by_event[event_type].append(event_data)

    summary = {
        "total_events": len(entries),
        "events_by_type": {
            event_type: len(events)
            for event_type, events in metrics_by_event.items()
        },
        "metrics": metrics_by_event,
    }

    with output_path.open("w") as f:
        json.dump(summary, f, indent=2, default=str)

