"""
Persistence utilities for Quadracode UI.

Handles storing and retrieving chat metadata, workspace descriptors,
and other persistent state in Redis.
"""

import json
from datetime import UTC, datetime
from typing import Any

import redis


# Redis key patterns
CHAT_METADATA_KEY = "qc:chat:metadata"
WORKSPACE_DESCRIPTOR_PREFIX = "qc:workspace:descriptors"


def load_chat_metadata(client: redis.Redis) -> dict[str, Any] | None:
    """
    Loads chat metadata from Redis.

    Args:
        client: Redis client instance.

    Returns:
        Chat metadata dict or None if not found.
    """
    try:
        data = client.hgetall(CHAT_METADATA_KEY)
        if not data:
            return None
        
        # Decode and parse
        metadata = {}
        for key, value in data.items():
            if key == "autonomous_settings":
                try:
                    metadata[key] = json.loads(value)
                except json.JSONDecodeError:
                    metadata[key] = None
            else:
                metadata[key] = value
        
        return metadata
    except redis.RedisError:
        return None


def save_chat_metadata(
    client: redis.Redis,
    chat_id: str,
    supervisor: str,
    autonomous_settings: dict[str, Any] | None = None,
) -> bool:
    """
    Saves chat metadata to Redis.

    Args:
        client: Redis client instance.
        chat_id: The chat ID.
        supervisor: The supervisor mode (human or human_clone).
        autonomous_settings: Optional autonomous mode settings.

    Returns:
        True if successful, False otherwise.
    """
    try:
        metadata = {
            "chat_id": chat_id,
            "supervisor": supervisor,
            "updated": datetime.now(UTC).isoformat(),
        }
        
        # Add created timestamp if not exists
        if not client.hexists(CHAT_METADATA_KEY, "created"):
            metadata["created"] = datetime.now(UTC).isoformat()
        
        if autonomous_settings:
            metadata["autonomous_settings"] = json.dumps(autonomous_settings)
        
        client.hset(CHAT_METADATA_KEY, mapping=metadata)
        return True
    except redis.RedisError:
        return False


def load_workspace_descriptor(
    client: redis.Redis,
    workspace_id: str,
) -> dict[str, Any] | None:
    """
    Loads a workspace descriptor from Redis.

    Args:
        client: Redis client instance.
        workspace_id: The workspace ID.

    Returns:
        Workspace descriptor dict or None if not found.
    """
    try:
        key = f"{WORKSPACE_DESCRIPTOR_PREFIX}:{workspace_id}"
        data = client.hgetall(key)
        if not data:
            return None
        
        # Decode fields
        descriptor = {}
        for k, v in data.items():
            key_str = k.decode("utf-8") if isinstance(k, bytes) else k
            val_str = v.decode("utf-8") if isinstance(v, bytes) else v
            
            # Attempt to parse JSON for complex fields
            if key_str in {"state", "files", "ports", "mounts"}:
                try:
                    descriptor[key_str] = json.loads(val_str)
                except (json.JSONDecodeError, TypeError):
                    descriptor[key_str] = val_str
            else:
                descriptor[key_str] = val_str
                
        return descriptor
    except redis.RedisError:
        return None


def save_workspace_descriptor(
    client: redis.Redis,
    workspace_id: str,
    descriptor: dict[str, Any],
) -> bool:
    """
    Saves a workspace descriptor to Redis.

    Args:
        client: Redis client instance.
        workspace_id: The workspace ID.
        descriptor: Workspace descriptor dict.

    Returns:
        True if successful, False otherwise.
    """
    try:
        key = f"{WORKSPACE_DESCRIPTOR_PREFIX}:{workspace_id}"
        
        # Prepare data for storage (serialize complex types)
        storage_data = {}
        for k, v in descriptor.items():
            if isinstance(v, (dict, list)):
                storage_data[k] = json.dumps(v)
            else:
                storage_data[k] = v
        
        # Add created timestamp if not in descriptor
        if "created" not in storage_data:
            storage_data["created"] = datetime.now(UTC).isoformat()
        
        storage_data["updated"] = datetime.now(UTC).isoformat()
        
        client.hset(key, mapping=storage_data)
        return True
    except redis.RedisError:
        return False


def load_all_workspace_descriptors(
    client: redis.Redis,
) -> dict[str, dict[str, Any]]:
    """
    Loads all workspace descriptors from Redis.

    Args:
        client: Redis client instance.

    Returns:
        Dict mapping workspace_id to descriptor.
    """
    try:
        pattern = f"{WORKSPACE_DESCRIPTOR_PREFIX}:*"
        descriptors = {}
        
        for key in client.scan_iter(match=pattern):
            # Extract workspace_id from key
            workspace_id = key.split(":")[-1]
            descriptor = load_workspace_descriptor(client, workspace_id)
            if descriptor:
                descriptors[workspace_id] = descriptor
        
        return descriptors
    except redis.RedisError:
        return {}


def delete_workspace_descriptor(
    client: redis.Redis,
    workspace_id: str,
) -> bool:
    """
    Deletes a workspace descriptor from Redis.

    Args:
        client: Redis client instance.
        workspace_id: The workspace ID.

    Returns:
        True if successful, False otherwise.
    """
    try:
        key = f"{WORKSPACE_DESCRIPTOR_PREFIX}:{workspace_id}"
        client.delete(key)
        return True
    except redis.RedisError:
        return False


def clear_all_context(client: redis.Redis) -> tuple[bool, str]:
    """
    Clears all Quadracode context from Redis.

    Deletes all keys matching qc:* pattern.

    Args:
        client: Redis client instance.

    Returns:
        Tuple of (success, message).
    """
    try:
        # Find all qc:* keys
        keys_to_delete = []
        for key in client.scan_iter(match="qc:*"):
            keys_to_delete.append(key)
        
        if keys_to_delete:
            client.delete(*keys_to_delete)
            return True, f"Deleted {len(keys_to_delete)} Redis keys"
        
        return True, "No keys to delete"
    except redis.RedisError as exc:
        return False, f"Failed to clear Redis: {exc}"


def load_message_history(
    client: redis.Redis,
    mailbox: str,
    chat_id: str,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """
    Loads message history from a Redis Stream mailbox.

    Args:
        client: Redis client instance.
        mailbox: The mailbox key to read from.
        chat_id: Filter messages by this chat_id.
        limit: Maximum number of messages to load.

    Returns:
        List of message dicts with role, content, timestamp, etc.
    """
    try:
        # Read from beginning of stream
        entries = client.xrevrange(mailbox, "+", "-", count=limit)
        
        messages = []
        for entry_id, fields in reversed(entries):  # Reverse to get chronological order
            # Parse envelope
            try:
                from quadracode_contracts import MessageEnvelope
                envelope = MessageEnvelope.from_stream_fields(fields)
                
                # Filter by chat_id
                if envelope.payload.get("chat_id") != chat_id:
                    continue
                
                # Determine role based on sender
                sender = envelope.sender
                if sender in {"human", "human_clone"}:
                    role = "user"
                else:
                    role = "assistant"
                
                # Extract trace if present
                trace = envelope.payload.get("messages")
                trace_list = trace if isinstance(trace, list) else None
                
                messages.append({
                    "role": role,
                    "content": envelope.message,
                    "sender": sender,
                    "timestamp": envelope.payload.get("timestamp") or fields.get("timestamp"),
                    "ticket_id": envelope.payload.get("ticket_id"),
                    "trace": trace_list,
                })
            except Exception:
                # Skip malformed messages
                continue
        
        return messages
    except redis.RedisError:
        return []

