"""Redis utilities for advanced E2E tests.

All helpers in this module operate directly through ``redis.Redis`` clients so the
advanced suite interacts with the running Docker stack via a single, canonical
code path.
"""

from __future__ import annotations

import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from redis import Redis
from redis.exceptions import RedisError

parent_tests = Path(__file__).resolve().parents[2]
if str(parent_tests) not in sys.path:
    sys.path.insert(0, str(parent_tests))

from test_end_to_end import SUPERVISOR_RECIPIENT, stream_id_gt  # noqa: E402

StreamEntry = tuple[str, dict[str, str]]
_STREAM_DUMP_LIMIT = 10_000


def _decode_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _decode_fields(raw_fields: Any) -> dict[str, str]:
    if isinstance(raw_fields, dict):
        items = raw_fields.items()
    elif isinstance(raw_fields, (list, tuple)):
        items = []
        for idx in range(0, len(raw_fields), 2):
            key = raw_fields[idx]
            if idx + 1 >= len(raw_fields):
                break
            value = raw_fields[idx + 1]
            items.append((key, value))
    else:
        return {}

    decoded: dict[str, str] = {}
    for key, value in items:
        if key is None or value is None:
            continue
        decoded[_decode_value(key)] = _decode_value(value)
    return decoded


def _decode_entry(entry_id: Any, fields: Any) -> StreamEntry:
    return (_decode_value(entry_id), _decode_fields(fields))


def _safe_xrange(client: Redis, stream: str, min_id: str, max_id: str, count: int) -> list[tuple[Any, Any]]:
    try:
        return client.xrange(stream, min=min_id, max=max_id, count=count)
    except RedisError:
        return []


def _safe_xrevrange(client: Redis, stream: str, count: int = 1) -> list[tuple[Any, Any]]:
    try:
        return client.xrevrange(stream, max="+", min="-", count=count)
    except RedisError:
        return []


def _read_stream_after(client: Redis, stream: str, baseline_id: str, count: int) -> list[StreamEntry]:
    min_id = f"({baseline_id}" if baseline_id else "-"
    entries = _safe_xrange(client, stream, min_id, "+", count)
    return [_decode_entry(entry_id, fields) for entry_id, fields in entries]


def send_message_to_orchestrator(
    redis_client: Redis,
    message: str,
    *,
    sender: str | None = None,
    reply_to: str | None = None,
) -> str:
    """Send a message to the orchestrator mailbox via Redis streams."""

    sender_name = sender or SUPERVISOR_RECIPIENT
    payload = {"supervisor": sender_name}
    if reply_to:
        payload["reply_to"] = reply_to

    entry_id = redis_client.xadd(
        "qc:mailbox/orchestrator",
        {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sender": sender_name,
            "recipient": "orchestrator",
            "message": message,
            "payload": json.dumps(payload, separators=(",", ":")),
        },
    )
    return _decode_value(entry_id)


def get_last_stream_id(redis_client: Redis, stream: str) -> str:
    """Return the last generated ID for the provided stream."""

    entries = _safe_xrevrange(redis_client, stream, count=1)
    if not entries:
        return "0-0"
    entry_id, _ = entries[0]
    return _decode_value(entry_id)


def read_stream(redis_client: Redis, stream: str, *, count: int = 20) -> list[StreamEntry]:
    """Read up to ``count`` records from ``stream``."""

    entries = _safe_xrange(redis_client, stream, "-", "+", count)
    return [_decode_entry(entry_id, fields) for entry_id, fields in entries]


def poll_stream_for_event(
    redis_client: Redis,
    stream: str,
    baseline_id: str,
    *,
    event_type: str,
    timeout: int = 60,
    poll_interval: int = 2,
) -> tuple[str, dict[str, str]] | None:
    """Poll a stream until an entry with ``event_type`` appears."""

    deadline = time.time() + timeout
    while time.time() < deadline:
        entries = _read_stream_after(redis_client, stream, baseline_id, count=200)
        for entry_id, fields in entries:
            if fields.get("event") == event_type:
                return entry_id, fields
        time.sleep(poll_interval)
    return None


def wait_for_message_on_stream(
    redis_client: Redis,
    stream: str,
    baseline_id: str,
    *,
    sender: str | None = None,
    recipient: str | None = None,
    timeout: int = 120,
    poll_interval: int = 2,
) -> dict[str, str]:
    """Wait for the next message on ``stream`` that matches the filters."""

    deadline = time.time() + timeout
    while time.time() < deadline:
        entries = _read_stream_after(redis_client, stream, baseline_id, count=100)
        for entry_id, fields in entries:
            if sender is not None and fields.get("sender") != sender:
                continue
            if recipient is not None and fields.get("recipient") != recipient:
                continue
            result = {"stream_id": entry_id}
            result.update(fields)
            return result
        time.sleep(poll_interval)

    filters = []
    if sender:
        filters.append(f"sender={sender}")
    if recipient:
        filters.append(f"recipient={recipient}")
    filter_str = ", ".join(filters) if filters else "any message"
    raise TimeoutError(
        f"Timed out waiting for message on stream '{stream}' ({filter_str}). "
        f"Baseline ID: {baseline_id}. Waited {timeout}s."
    )


def dump_all_streams(
    redis_client: Redis,
    output_dir: Path,
    *,
    stream_pattern: str = "qc:*",
) -> None:
    """Dump every Redis stream matching ``stream_pattern`` into JSON files."""

    output_dir.mkdir(parents=True, exist_ok=True)
    stream_names = sorted({_decode_value(name) for name in redis_client.scan_iter(match=stream_pattern)})

    for stream_name in stream_names:
        entries = read_stream(redis_client, stream_name, count=_STREAM_DUMP_LIMIT)
        if not entries:
            continue
        _write_stream_dump(output_dir, stream_name, entries)


def _write_stream_dump(output_dir: Path, stream_name: str, entries: Sequence[StreamEntry]) -> None:
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
    with output_file.open("w") as handle:
        json.dump(stream_data, handle, indent=2, default=str)


def validate_stream_monotonicity(
    redis_client: Redis,
    stream: str,
    *,
    count: int = 1000,
) -> bool:
    """Ensure stream IDs are strictly increasing."""

    entries = read_stream(redis_client, stream, count=count)
    if len(entries) < 2:
        return True

    prev_id = entries[0][0]
    for entry_id, _ in entries[1:]:
        if not stream_id_gt(entry_id, prev_id):
            return False
        prev_id = entry_id
    return True


def export_stream_to_csv(
    redis_client: Redis,
    stream: str,
    output_path: Path,
    *,
    count: int = 10_000,
) -> None:
    """Export a stream to CSV for easier auditing."""

    entries = read_stream(redis_client, stream, count=count)
    if not entries:
        return

    fieldnames = {"stream_id"}
    for _, fields in entries:
        fieldnames.update(fields.keys())
    headers = ["stream_id"] + sorted(fieldnames - {"stream_id"})

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for entry_id, fields in entries:
            row = {"stream_id": entry_id}
            row.update(fields)
            writer.writerow(row)


def get_stream_stats(redis_client: Redis, stream: str) -> dict[str, Any]:
    """Return length, first ID, and last ID for ``stream``."""

    try:
        entry_count = int(redis_client.xlen(stream))
    except RedisError:
        entry_count = 0

    first_entries = read_stream(redis_client, stream, count=1)
    first_id = first_entries[0][0] if first_entries else None

    last_entries = _safe_xrevrange(redis_client, stream, count=1)
    last_id = _decode_value(last_entries[0][0]) if last_entries else None

    return {
        "entry_count": entry_count,
        "first_id": first_id,
        "last_id": last_id,
        "length": entry_count,
    }
