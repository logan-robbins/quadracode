"""Logging framework for advanced E2E tests.

This module provides comprehensive logging utilities for capturing test execution,
including turn-by-turn conversation logs, tool calls, and stream snapshots.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def create_test_log_directory(test_name: str, base_dir: Path | None = None) -> Path:
    """Create a timestamped log directory for a test.

    Args:
        test_name: Name of the test (e.g., "test_sustained_orchestrator_agent_ping_pong")
        base_dir: Base directory for logs. Defaults to tests/e2e_advanced/logs/

    Returns:
        Path to the created log directory

    Example:
        >>> log_dir = create_test_log_directory("test_my_feature")
        >>> # Returns: tests/e2e_advanced/logs/test_my_feature_20251115-123456/
    """
    if base_dir is None:
        base_dir = Path(__file__).resolve().parents[2] / "logs"

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    log_dir = base_dir / f"{test_name}_{timestamp}"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Configure Python logger for this test with both file and console handlers
    logger = logging.getLogger(test_name)
    logger.setLevel(logging.DEBUG)

    # File handler with detailed format
    file_handler = logging.FileHandler(log_dir / "test.log")
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # Console handler with simpler format
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    logger.info("Created test log directory: %s", log_dir)
    return log_dir


def log_turn(
    log_dir: Path,
    turn_number: int,
    message: dict[str, Any],
    response: dict[str, Any],
    duration_ms: int | None = None,
    context_metrics: dict[str, Any] | None = None,
) -> None:
    """Log a single conversation turn with message, response, and metrics.

    Args:
        log_dir: Directory to write turn logs
        turn_number: Sequential turn number (1-indexed)
        message: Message envelope dict with keys: stream_id, sender, recipient, message, payload
        response: Response envelope dict with same structure
        duration_ms: Optional duration of the turn in milliseconds
        context_metrics: Optional context engineering metrics for this turn

    Example:
        >>> log_turn(
        ...     log_dir=Path("logs/test_123"),
        ...     turn_number=1,
        ...     message={"sender": "human", "recipient": "orchestrator", "message": "Hello"},
        ...     response={"sender": "orchestrator", "recipient": "human", "message": "Hi!"},
        ...     duration_ms=1234,
        ... )
    """
    turn_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "turn_number": turn_number,
        "duration_ms": duration_ms,
        "message": message,
        "response": response,
    }

    if context_metrics is not None:
        turn_data["context_metrics"] = context_metrics

    turn_file = log_dir / f"turn_{turn_number:03d}.json"
    with turn_file.open("w") as f:
        json.dump(turn_data, f, indent=2, default=str)


def log_stream_snapshot(
    log_dir: Path, stream_name: str, entries: list[tuple[str, dict[str, str]]]
) -> None:
    """Log a snapshot of Redis stream entries.

    Args:
        log_dir: Directory to write snapshot
        stream_name: Name of the Redis stream (e.g., "qc:mailbox/orchestrator")
        entries: List of (entry_id, fields_dict) tuples from stream

    Example:
        >>> entries = [
        ...     ("1234567890-0", {"sender": "human", "message": "Hello"}),
        ...     ("1234567891-0", {"sender": "orchestrator", "message": "Hi"}),
        ... ]
        >>> log_stream_snapshot(Path("logs/test_123"), "qc:mailbox/human", entries)
    """
    snapshot_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stream": stream_name,
        "entry_count": len(entries),
        "entries": [
            {
                "id": entry_id,
                "fields": fields,
            }
            for entry_id, fields in entries
        ],
    }

    # Sanitize stream name for filename (replace : and / with _)
    safe_stream_name = stream_name.replace(":", "_").replace("/", "_")
    snapshot_file = log_dir / f"{safe_stream_name}_snapshot.json"

    with snapshot_file.open("w") as f:
        json.dump(snapshot_data, f, indent=2, default=str)


def log_tool_call(
    log_dir: Path,
    tool_name: str,
    inputs: dict[str, Any],
    outputs: dict[str, Any] | str,
    duration_ms: int,
    success: bool = True,
) -> None:
    """Log a single tool invocation with inputs, outputs, and duration.

    Args:
        log_dir: Directory to write tool call logs
        tool_name: Name of the tool (e.g., "workspace_exec", "read_file")
        inputs: Tool input parameters
        outputs: Tool output (dict or string)
        duration_ms: Execution time in milliseconds
        success: Whether the tool call succeeded

    Example:
        >>> log_tool_call(
        ...     log_dir=Path("logs/test_123"),
        ...     tool_name="workspace_exec",
        ...     inputs={"command": "ls -la", "workspace_id": "ws-test"},
        ...     outputs={"stdout": "file1.py\\nfile2.py", "exit_code": 0},
        ...     duration_ms=234,
        ...     success=True,
        ... )
    """
    timestamp = datetime.now(timezone.utc)
    tool_data = {
        "timestamp": timestamp.isoformat(),
        "tool_name": tool_name,
        "duration_ms": duration_ms,
        "success": success,
        "inputs": inputs,
        "outputs": outputs,
    }

    # Use timestamp in filename to ensure uniqueness for multiple calls to same tool
    timestamp_str = timestamp.strftime("%Y%m%d-%H%M%S-%f")
    tool_file = log_dir / f"tool_call_{tool_name}_{timestamp_str}.json"

    with tool_file.open("w") as f:
        json.dump(tool_data, f, indent=2, default=str)


def configure_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Configure a logger with ISO timestamp format.

    Args:
        name: Logger name (typically __name__ or test name)
        level: Logging level (default: INFO)

    Returns:
        Configured logger instance

    Example:
        >>> logger = configure_logger("test_my_feature", logging.DEBUG)
        >>> logger.info("Test starting...")
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid duplicate handlers if logger already configured
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(level)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger

