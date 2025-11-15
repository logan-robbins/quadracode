"""Redis utilities for advanced E2E tests.

This module extends the base Redis helpers from tests/test_end_to_end.py with
additional functionality for polling events, validating streams, and exporting data.
"""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any

# Re-export base utilities from parent test module
import sys
parent_tests = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(parent_tests))
from test_end_to_end import (
    get_last_stream_id,
    read_stream,
    read_stream_after,
    redis_cli,
    stream_entries_added,
    stream_id_gt,
    stream_info,
)


def poll_stream_for_event(
    stream: str,
    baseline_id: str,
    event_type: str,
    timeout: int = 60,
    poll_interval: int = 2,
) -> tuple[str, dict[str, str]] | None:
    """Poll a stream until an event with matching event type is found.

    Args:
        stream: Redis stream name (e.g., "qc:context:metrics")
        baseline_id: Stream ID to start searching from
        event_type: Value to match in the "event" field
        timeout: Maximum seconds to wait
        poll_interval: Seconds between polls

    Returns:
        Tuple of (entry_id, fields_dict) if found, None if timeout

    Example:
        >>> entry = poll_stream_for_event(
        ...     stream="qc:context:metrics",
        ...     baseline_id="1234567890-0",
        ...     event_type="curation",
        ...     timeout=120,
        ... )
        >>> if entry:
        ...     entry_id, fields = entry
        ...     print(f"Found curation event: {entry_id}")
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        entries = read_stream_after(stream, baseline_id, count=200)
        for entry_id, fields in entries:
            if fields.get("event") == event_type:
                return (entry_id, fields)
        time.sleep(poll_interval)
    return None


def wait_for_message_on_stream(
    stream: str,
    baseline_id: str,
    sender: str | None = None,
    recipient: str | None = None,
    timeout: int = 120,
    poll_interval: int = 2,
) -> dict[str, str]:
    """Poll a stream for a message from a specific sender and/or to a recipient.

    Args:
        stream: Redis stream name (e.g., "qc:mailbox/human_clone")
        baseline_id: Stream ID to start searching from
        sender: Optional sender to match (e.g., "orchestrator")
        recipient: Optional recipient to match (e.g., "human_clone")
        timeout: Maximum seconds to wait
        poll_interval: Seconds between polls

    Returns:
        Fields dict of the matching message

    Raises:
        TimeoutError: If no matching message found within timeout

    Example:
        >>> fields = wait_for_message_on_stream(
        ...     stream="qc:mailbox/human_clone",
        ...     baseline_id="0-0",
        ...     sender="orchestrator",
        ...     timeout=180,
        ... )
        >>> print(f"Orchestrator said: {fields['message']}")
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        entries = read_stream_after(stream, baseline_id, count=100)
        for entry_id, fields in entries:
            # Check sender and recipient match if specified
            if sender is not None and fields.get("sender") != sender:
                continue
            if recipient is not None and fields.get("recipient") != recipient:
                continue
            # Found matching message
            return fields
        time.sleep(poll_interval)

    # Build detailed error message
    filters = []
    if sender:
        filters.append(f"sender={sender}")
    if recipient:
        filters.append(f"recipient={recipient}")
    filter_str = ", ".join(filters) if filters else "any message"

    raise TimeoutError(
        f"Timed out waiting for message on stream '{stream}' with {filter_str}. "
        f"Waited {timeout}s. Check that the sender service is running and "
        f"routing messages correctly. Baseline ID: {baseline_id}"
    )


def dump_all_streams(output_dir: Path, stream_pattern: str = "qc:*") -> None:
    """Read all streams matching pattern and write to JSON files.

    Args:
        output_dir: Directory to write stream dumps
        stream_pattern: Redis key pattern (default: "qc:*")

    Example:
        >>> dump_all_streams(Path("artifacts/test_123"))
        # Creates: qc_mailbox_orchestrator.json, qc_context_metrics.json, etc.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get list of all streams matching pattern
    proc = redis_cli("--json", "KEYS", stream_pattern, check=False)
    if proc.returncode != 0 or not proc.stdout.strip():
        return

    try:
        keys = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return

    if not isinstance(keys, list):
        return

    # Dump each stream
    for stream_name in keys:
        if not isinstance(stream_name, str):
            continue

        entries = read_stream(stream_name, count=10000)
        if not entries:
            continue

        # Sanitize stream name for filename
        safe_name = stream_name.replace(":", "_").replace("/", "_")
        output_file = output_dir / f"{safe_name}.json"

        stream_data = {
            "stream": stream_name,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "entry_count": len(entries),
            "entries": [
                {
                    "id": entry_id,
                    "fields": fields,
                }
                for entry_id, fields in entries
            ],
        }

        with output_file.open("w") as f:
            json.dump(stream_data, f, indent=2, default=str)


def validate_stream_monotonicity(stream: str, count: int = 1000) -> bool:
    """Validate that stream IDs are strictly increasing (no gaps or reorders).

    Args:
        stream: Redis stream name
        count: Number of recent entries to check

    Returns:
        True if monotonic, False if gaps or reorders detected

    Example:
        >>> is_valid = validate_stream_monotonicity("qc:mailbox/orchestrator")
        >>> assert is_valid, "Stream has gaps or reordering issues"
    """
    entries = read_stream(stream, count=count)
    if len(entries) < 2:
        return True  # Empty or single entry is trivially monotonic

    prev_id = entries[0][0]
    for entry_id, _ in entries[1:]:
        if not stream_id_gt(entry_id, prev_id):
            return False
        prev_id = entry_id

    return True


def export_stream_to_csv(stream: str, output_path: Path, count: int = 10000) -> None:
    """Export a Redis stream to CSV for human-readable audit logs.

    Args:
        stream: Redis stream name
        output_path: Path to write CSV file
        count: Maximum entries to export

    Example:
        >>> export_stream_to_csv(
        ...     stream="qc:mailbox/orchestrator",
        ...     output_path=Path("artifacts/orchestrator_messages.csv"),
        ... )
    """
    entries = read_stream(stream, count=count)
    if not entries:
        return

    # Determine all field names across entries
    all_fields = set()
    for _, fields in entries:
        all_fields.update(fields.keys())
    fieldnames = ["stream_id"] + sorted(all_fields)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for entry_id, fields in entries:
            row = {"stream_id": entry_id}
            row.update(fields)
            writer.writerow(row)


def get_stream_stats(stream: str) -> dict[str, Any]:
    """Get comprehensive stats for a stream.

    Args:
        stream: Redis stream name

    Returns:
        Dict with keys: entry_count, first_id, last_id, length

    Example:
        >>> stats = get_stream_stats("qc:mailbox/orchestrator")
        >>> print(f"Stream has {stats['entry_count']} entries")
    """
    info = stream_info(stream)
    if not info:
        return {
            "entry_count": 0,
            "first_id": None,
            "last_id": None,
            "length": 0,
        }

    entry_count = stream_entries_added(stream) or 0
    return {
        "entry_count": entry_count,
        "first_id": info.get("first-entry-id") or info.get("first_entry_id"),
        "last_id": info.get("last-generated-id") or info.get("last_generated_id"),
        "length": int(info.get("length", 0)),
    }

