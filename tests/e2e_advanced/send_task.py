#!/usr/bin/env python3
"""
Simple task sender for E2E testing.

This script sends a task to the orchestrator and monitors for responses.
It's designed to run inside the Docker stack (e.g., from orchestrator-runtime container).
"""
import sys
import json
import time
from datetime import datetime, timezone

import redis


def send_task(task_description: str, timeout: int = 300) -> bool:
    """
    Send a task to orchestrator and monitor results.
    
    Args:
        task_description: The task to send
        timeout: Maximum time to wait for response in seconds
    
    Returns:
        True if response received, False if timeout
    """
    client = redis.Redis(host="redis", port=6379, decode_responses=True)
    
    # Get baseline
    baseline = client.xrevrange("qc:mailbox/human", count=1)
    baseline_id = baseline[0][0] if baseline else "0-0"
    
    # Send message to orchestrator
    timestamp = datetime.now(timezone.utc).isoformat()
    payload = json.dumps({"supervisor": "human"})
    
    client.xadd(
        "qc:mailbox/orchestrator",
        {
            "timestamp": timestamp,
            "sender": "human",
            "recipient": "orchestrator",
            "message": task_description,
            "payload": payload,
        }
    )
    
    print(f"✓ Sent task to orchestrator")
    print(f"  Task: {task_description[:80]}...")
    print(f"→ Monitoring qc:mailbox/human for response...")
    print()
    
    # Poll for response
    start = time.time()
    message_count = 0
    
    while time.time() - start < timeout:
        elapsed = int(time.time() - start)
        messages = client.xread(
            {"qc:mailbox/human": baseline_id}, 
            count=10, 
            block=2000
        )
        
        if messages:
            for stream, entries in messages:
                for entry_id, fields in entries:
                    message_count += 1
                    msg_text = fields.get("message", "")
                    
                    print(f"[{elapsed}s] ✓ Response #{message_count}")
                    print(f"  From: {fields.get('sender', 'unknown')}")
                    print(f"  Message: {msg_text[:200]}")
                    
                    # Check payload for additional info
                    payload_str = fields.get("payload", "{}")
                    try:
                        payload_data = json.loads(payload_str)
                        if "messages" in payload_data:
                            print(f"  Trace entries: {len(payload_data['messages'])}")
                    except json.JSONDecodeError:
                        pass
                    
                    print()
                    baseline_id = entry_id
                    
                    # Check for completion indicators
                    if any(word in msg_text.lower() for word in ["completed", "finished", "done"]):
                        print(f"✓ Task appears complete after {elapsed}s")
                        return True
        
        # Print progress every 30 seconds
        if elapsed > 0 and elapsed % 30 == 0:
            print(f"[{elapsed}s] Still waiting... (received {message_count} messages so far)")
    
    print(f"✗ Timeout after {timeout}s (received {message_count} messages)")
    return False


def main():
    """Main entry point."""
    task = """
Build a system that calculates derivatives of stock prices at 5s, 30s, and 2m intervals.

Requirements:
- Create 3 separate Python modules, one for each time interval (derivative_5s.py, derivative_30s.py, derivative_2m.py)
- Each module should have a calculate_derivative(prices: list[float]) -> list[float] function
- Use numpy for numerical differentiation
- Include unit tests for each module with sample data
- Add a main.py that demonstrates using all three modules

Please create a plan, consider if you need multiple agents to work in parallel, and implement the solution.
"""
    
    if len(sys.argv) > 1:
        task = sys.argv[1]
    
    success = send_task(task)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

