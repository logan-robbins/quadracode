"""
Realistic end-to-end workflow tests.

These tests simulate actual user interactions by sending tasks to the orchestrator
and monitoring the Redis streams for responses, agent spawning, and completion.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest
import redis


@pytest.mark.e2e_advanced
def test_multi_agent_parallel_work(docker_stack, redis_client):
    """
    Test orchestrator spawning multiple agents for parallel work.
    
    This test validates:
    - Orchestrator receives and processes complex task
    - Orchestrator creates a plan for parallel work
    - Orchestrator spawns agents dynamically (if needed)
    - Agents work on their assigned tasks
    - Orchestrator reviews and integrates results
    - Final response is sent to human
    
    Expected duration: 5-10 minutes depending on task complexity
    """
    print("\n" + "=" * 70)
    print("TEST: Multi-Agent Parallel Work")
    print("=" * 70)
    
    # Get baselines
    baseline_human = redis_client.xrevrange("qc:mailbox/human", count=1)
    baseline_human_id = baseline_human[0][0] if baseline_human else "0-0"
    
    baseline_orch = redis_client.xrevrange("qc:mailbox/orchestrator", count=1)
    baseline_orch_id = baseline_orch[0][0] if baseline_orch else "0-0"
    
    # Send complex task requiring multiple agents
    task = """
Build a system that calculates derivatives of stock prices at 5s, 30s, and 2m intervals.

Requirements:
- Create 3 separate Python modules (derivative_5s.py, derivative_30s.py, derivative_2m.py)
- Each module should have a calculate_derivative(prices: list[float]) -> list[float] function
- Use numpy for numerical differentiation
- Include unit tests for each module
- Add a main.py that demonstrates all three

Please create a plan, spawn agents if needed to work in parallel, and implement the solution.
"""
    
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
    
    print("✓ Task sent to orchestrator")
    print(f"  Task: {task.split('Requirements:')[0].strip()}")
    print()
    
    # Monitor for orchestrator activity
    spawned_agents = []
    human_messages = []
    agent_messages = {}
    tool_calls = []
    
    timeout = 600  # 10 minutes
    start = time.time()
    last_log = start
    
    while time.time() - start < timeout:
        elapsed = int(time.time() - start)
        
        # Check orchestrator's mailbox for outgoing messages
        orch_messages = redis_client.xread(
            {"qc:mailbox/orchestrator": baseline_orch_id}, 
            count=100, 
            block=1000
        )
        
        for stream, entries in orch_messages:
            for entry_id, fields in entries:
                payload_str = fields.get("payload", "{}")
                try:
                    payload_data = json.loads(payload_str)
                    messages = payload_data.get("messages", [])
                    
                    # Look for tool calls
                    for msg in messages:
                        if isinstance(msg, dict):
                            msg_type = msg.get("type")
                            
                            if msg_type == "tool":
                                tool_data = msg.get("data", {})
                                tool_name = tool_data.get("name", "unknown")
                                tool_calls.append(tool_name)
                                
                                # Check for agent management
                                if tool_name == "agent_management":
                                    try:
                                        args = json.loads(tool_data.get("input", "{}"))
                                        operation = args.get("operation")
                                        agent_name = args.get("agent_name", "unknown")
                                        
                                        if operation == "spawn":
                                            spawned_agents.append(agent_name)
                                            print(f"[{elapsed}s] ✓ Orchestrator spawned agent: {agent_name}")
                                        elif operation == "list":
                                            print(f"[{elapsed}s] → Orchestrator listing agents")
                                    except (json.JSONDecodeError, KeyError):
                                        pass
                            
                            elif msg_type == "ai":
                                # AI reasoning message
                                content = msg.get("data", {}).get("content", "")
                                if content and len(content) > 50:
                                    print(f"[{elapsed}s] → Orchestrator reasoning: {content[:80]}...")
                
                except json.JSONDecodeError:
                    pass
                
                baseline_orch_id = entry_id
        
        # Check for messages to spawned agents
        for agent_name in spawned_agents:
            if agent_name not in agent_messages:
                agent_messages[agent_name] = []
            
            agent_mailbox = f"qc:mailbox/{agent_name}"
            agent_baseline = agent_messages[agent_name][-1] if agent_messages[agent_name] else "0-0"
            
            agent_msgs = redis_client.xread(
                {agent_mailbox: agent_baseline},
                count=10,
                block=100
            )
            
            if agent_msgs:
                for stream, entries in agent_msgs:
                    for entry_id, fields in entries:
                        agent_messages[agent_name].append(entry_id)
                        sender = fields.get("sender", "unknown")
                        msg = fields.get("message", "")
                        print(f"[{elapsed}s] → {sender} to {agent_name}: {msg[:60]}...")
        
        # Check for completion message to human
        human_msgs = redis_client.xread(
            {"qc:mailbox/human": baseline_human_id},
            count=10,
            block=1000
        )
        
        if human_msgs:
            for stream, entries in human_msgs:
                for entry_id, fields in entries:
                    message = fields.get("message", "")
                    sender = fields.get("sender", "orchestrator")
                    human_messages.append(message)
                    
                    print(f"\n[{elapsed}s] ✓ Message to human from {sender}")
                    print(f"  {message[:150]}...")
                    
                    # Check for completion indicators
                    completion_words = ["completed", "finished", "done", "implemented", "created"]
                    if any(word in message.lower() for word in completion_words):
                        print(f"\n{'=' * 70}")
                        print(f"✓ Task completed after {elapsed}s!")
                        print(f"{'=' * 70}")
                        print(f"\nSummary:")
                        print(f"  - Agents spawned: {len(spawned_agents)} ({', '.join(spawned_agents) if spawned_agents else 'none'})")
                        print(f"  - Tool calls made: {len(tool_calls)}")
                        print(f"  - Messages to human: {len(human_messages)}")
                        print(f"  - Agent messages: {sum(len(msgs) for msgs in agent_messages.values())}")
                        
                        # Assertions
                        assert len(human_messages) >= 1, "Should have at least one message to human"
                        assert "derivative" in message.lower(), "Response should mention derivatives"
                        
                        print(f"\n✓ All validations passed!")
                        return
                    
                    baseline_human_id = entry_id
        
        # Log progress every 30 seconds
        if elapsed - (last_log - start) >= 30:
            print(f"\n[{elapsed}s] Progress update:")
            print(f"  - Spawned agents: {len(spawned_agents)}")
            print(f"  - Tool calls: {len(tool_calls)}")
            print(f"  - Messages to human: {len(human_messages)}")
            last_log = time.time()
        
        time.sleep(2)
    
    # Timeout - print diagnostic info
    print(f"\n✗ Timeout after {timeout}s")
    print(f"\nDiagnostics:")
    print(f"  - Spawned agents: {spawned_agents}")
    print(f"  - Tool calls: {tool_calls}")
    print(f"  - Messages to human: {len(human_messages)}")
    print(f"  - Last human message: {human_messages[-1][:200] if human_messages else 'none'}")
    
    pytest.fail(f"Test timeout after {timeout}s without completion")


@pytest.mark.e2e_advanced
def test_simple_task_without_spawning(docker_stack, redis_client):
    """
    Test orchestrator handling a simple task without needing to spawn agents.
    
    This validates basic orchestrator functionality without the complexity
    of dynamic agent spawning.
    
    Expected duration: 1-2 minutes
    """
    print("\n" + "=" * 70)
    print("TEST: Simple Task Without Agent Spawning")
    print("=" * 70)
    
    # Get baseline
    baseline_human = redis_client.xrevrange("qc:mailbox/human", count=1)
    baseline_human_id = baseline_human[0][0] if baseline_human else "0-0"
    
    # Send simple task
    task = "Please explain what a derivative of a function means in calculus, in simple terms."
    
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
    
    print(f"✓ Sent task: {task}")
    print("→ Waiting for response...")
    print()
    
    # Monitor for response
    timeout = 120  # 2 minutes
    start = time.time()
    
    while time.time() - start < timeout:
        elapsed = int(time.time() - start)
        
        messages = redis_client.xread(
            {"qc:mailbox/human": baseline_human_id},
            count=10,
            block=2000
        )
        
        if messages:
            for stream, entries in messages:
                for entry_id, fields in entries:
                    message = fields.get("message", "")
                    
                    print(f"[{elapsed}s] ✓ Response received")
                    print(f"  Message: {message[:200]}...")
                    
                    # Validate response
                    assert len(message) > 50, "Response should be substantial"
                    assert "derivative" in message.lower(), "Response should mention derivatives"
                    
                    print(f"\n✓ Test passed after {elapsed}s!")
                    return
        
        time.sleep(2)
    
    pytest.fail(f"No response after {timeout}s")


@pytest.mark.e2e_advanced  
@pytest.mark.slow
def test_orchestrator_delegation_to_existing_agent(docker_stack, redis_client):
    """
    Test orchestrator delegating work to the existing agent-runtime.
    
    This validates the orchestrator can delegate work without spawning new agents.
    
    Expected duration: 2-3 minutes
    """
    print("\n" + "=" * 70)
    print("TEST: Orchestrator Delegation to Existing Agent")
    print("=" * 70)
    
    # Get baselines
    baseline_human = redis_client.xrevrange("qc:mailbox/human", count=1)
    baseline_human_id = baseline_human[0][0] if baseline_human else "0-0"
    
    baseline_agent = redis_client.xrevrange("qc:mailbox/agent-runtime", count=1)
    baseline_agent_id = baseline_agent[0][0] if baseline_agent else "0-0"
    
    # Send task that should be delegated
    task = """
Write a simple Python function that calculates the numerical derivative of a list of values.
Use numpy and include a docstring with example usage.
"""
    
    timestamp = datetime.now(timezone.utc).isoformat()
    payload = json.dumps({"supervisor": "human", "reply_to": "agent-runtime"})
    
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
    
    print(f"✓ Sent task with reply_to=agent-runtime")
    print("→ Monitoring for delegation and response...")
    print()
    
    delegated = False
    timeout = 180  # 3 minutes
    start = time.time()
    
    while time.time() - start < timeout:
        elapsed = int(time.time() - start)
        
        # Check if orchestrator delegated to agent
        agent_messages = redis_client.xread(
            {"qc:mailbox/agent-runtime": baseline_agent_id},
            count=10,
            block=1000
        )
        
        if agent_messages and not delegated:
            for stream, entries in agent_messages:
                for entry_id, fields in entries:
                    if fields.get("sender") == "orchestrator":
                        delegated = True
                        print(f"[{elapsed}s] ✓ Orchestrator delegated to agent-runtime")
                    baseline_agent_id = entry_id
        
        # Check for final response to human
        human_messages = redis_client.xread(
            {"qc:mailbox/human": baseline_human_id},
            count=10,
            block=1000
        )
        
        if human_messages:
            for stream, entries in human_messages:
                for entry_id, fields in entries:
                    message = fields.get("message", "")
                    
                    print(f"[{elapsed}s] ✓ Response to human received")
                    print(f"  Message: {message[:150]}...")
                    
                    # Validate
                    assert delegated, "Orchestrator should have delegated to agent"
                    assert "def " in message or "function" in message.lower(), \
                        "Response should include Python function"
                    
                    print(f"\n✓ Test passed after {elapsed}s!")
                    return
        
        time.sleep(2)
    
    pytest.fail(f"No response after {timeout}s. Delegated: {delegated}")

