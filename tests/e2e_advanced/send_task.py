#!/usr/bin/env python3
"""
Simple task sender for E2E testing.

Sends a message to orchestrator. Use scripts/tail_streams.sh to watch message flow.
"""
import sys
import json
from datetime import datetime, timezone

import redis


def send_task(task_description: str) -> str:
    """
    Send a task to orchestrator.
    
    Args:
        task_description: The task to send
    
    Returns:
        Message ID added to qc:mailbox/orchestrator
    """
    client = redis.Redis(host="redis", port=6379, decode_responses=True)
    
    timestamp = datetime.now(timezone.utc).isoformat()
    payload = json.dumps({"supervisor": "human"})
    
    message_id = client.xadd(
        "qc:mailbox/orchestrator",
        {
            "timestamp": timestamp,
            "sender": "human",
            "recipient": "orchestrator",
            "message": task_description,
            "payload": payload,
        }
    )
    
    return message_id


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
        task = " ".join(sys.argv[1:])
    
    print(f"Task: {task[:100]}...")
    message_id = send_task(task)
    print(f"✓ Sent to qc:mailbox/orchestrator (ID: {message_id})")
    print()
    print("Watch message flow:")
    print("  ./scripts/tail_streams.sh")


if __name__ == "__main__":
    main()

