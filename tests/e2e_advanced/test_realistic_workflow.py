"""
Simple E2E test: validate message flow through Redis Streams.

Tests the core purpose: send message to orchestrator, receive response.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest


@pytest.mark.e2e_advanced
def test_message_flow(docker_stack, redis_client):
    """
    Validate basic message flow: human → orchestrator → human.
    
    This is the singular purpose of the system: messages flow through
    Redis Streams between orchestrator and agents. If this works, the
    communication fabric works.
    """
    # Get baseline
    baseline = redis_client.xrevrange("qc:mailbox/human", count=1)
    baseline_id = baseline[0][0] if baseline else "0-0"
    
    # Send message
    task = "Explain what a derivative is in calculus."
    timestamp = datetime.now(timezone.utc).isoformat()
    payload = json.dumps({"supervisor": "human"})
    
    redis_client.xadd(
        "qc:mailbox/orchestrator",
        {
            "timestamp": timestamp,
            "sender": "human",
            "recipient": "orchestrator",
            "message": task,
            "payload": payload,
        }
    )
    
    print(f"\n✓ Sent: {task}")
    
    # Wait for response (any response)
    timeout = 120  # 2 minutes
    import time
    start = time.time()
    
    while time.time() - start < timeout:
        messages = redis_client.xread(
            {"qc:mailbox/human": baseline_id},
            count=10,
            block=2000
        )
        
        if messages:
            for stream, entries in messages:
                for entry_id, fields in entries:
                    message = fields.get("message", "")
                    print(f"✓ Response received: {message[:100]}...")
                    
                    # Message flow validated: human → orch → human
                    assert len(message) > 0, "Response should not be empty"
                    return
        
        time.sleep(2)
    
    pytest.fail(f"No response after {timeout}s")
