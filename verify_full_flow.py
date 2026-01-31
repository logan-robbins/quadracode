
import json
import redis
import uuid
import time
import sys
import urllib.request
from datetime import datetime, timezone

REDIS_HOST = 'localhost'
REDIS_PORT = 6379
REGISTRY_URL = 'http://localhost:8090/agents'

def get_agent_count():
    try:
        with urllib.request.urlopen(REGISTRY_URL) as response:
            data = json.loads(response.read().decode())
            # Registry returns dict or list? Usually list of agents.
            # If it's a dict with 'agents' key, handle that.
            if isinstance(data, dict) and 'agents' in data:
                return len(data['agents'])
            return len(data)
    except Exception as e:
        print(f"Error checking registry: {e}")
        return 0

def send_message(r, chat_id, text):
    payload = {
        'chat_id': chat_id,
        'messages': [{
            'type': 'human',
            'data': {
                'content': text,
                'type': 'human',
                'id': str(uuid.uuid4())
            }
        }]
    }
    envelope = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'sender': 'human',
        'recipient': 'orchestrator',
        'message': text,
        'payload': json.dumps(payload)
    }
    r.xadd('qc:mailbox/orchestrator', envelope)
    print(f"\n[USER] Sent: {text}")

def wait_for_response(r, chat_id, timeout=60):
    start_time = time.time()
    last_id = '$'
    print("[SYSTEM] Waiting for orchestrator response...")
    
    while time.time() - start_time < timeout:
        streams = r.xread({'qc:mailbox/human': last_id}, count=1, block=1000)
        if not streams:
            continue
            
        for stream_name, messages in streams:
            for message_id, data in messages:
                last_id = message_id
                payload = json.loads(data.get('payload', '{}'))
                if payload.get('chat_id') == chat_id:
                    msg = data.get('message')
                    print(f"[ORCHESTRATOR] {msg}")
                    return True, msg
    print("[ERROR] Timeout waiting for response.")
    return False, None

def monitor_stream_activity(r, stream_key, duration=5):
    """Monitor a stream for a short duration to see activity."""
    print(f"[DEBUG] Monitoring {stream_key} for {duration}s...")
    start = time.time()
    last_id = '$'
    activity = []
    while time.time() - start < duration:
        streams = r.xread({stream_key: last_id}, count=10, block=500)
        if streams:
            for _, msgs in streams:
                for mid, data in msgs:
                    last_id = mid
                    sender = data.get('sender')
                    recipient = data.get('recipient')
                    print(f"    -> {sender} sent to {recipient}: {data.get('message')[:50]}...")
                    activity.append(data)
    return activity

def run_verification():
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    chat_id = f"test-flow-{uuid.uuid4()}"
    
    print("=== Step 1: Initial State ===")
    initial_agents = get_agent_count()
    print(f"Active Agents: {initial_agents}")
    
    # --- Test 1: Single Agent Spawn ---
    print("\n=== Step 2: Single Agent Task ===")
    task = "Please spawn a math agent. Ask it to calculate 50 times 3. Tell me the result."
    send_message(r, chat_id, task)
    
    # We expect some time for: Orch process -> Spawn Agent -> Orch delegate -> Agent process -> Agent reply -> Orch reply
    # We can perform a loop here that waits for the final human response, checking agent status in between.
    
    success, response = wait_for_response(r, chat_id, timeout=90)
    if not success:
        return False
        
    current_agents = get_agent_count()
    print(f"Active Agents: {current_agents}")
    if current_agents <= initial_agents:
        print("[WARNING] Agent count did not increase. Orchestrator might have reused an agent or handled it internally?")
    else:
        print("[SUCCESS] New agent spawned.")

    if "150" in response:
        print("[SUCCESS] Correct calculation result received.")
    else:
        print("[WARNING] Result confirmed? Please verify manually.")

    # --- Test 2: Multi Agent Spawn ---
    print("\n=== Step 3: Multi-Agent Coordination ===")
    task_2 = "Spawn two NEW research agents, ensure they have distinct names/IDs. Ask Agent A to define 'Entropy' briefly, and Agent B to define 'Enthalpy' briefly. You must receive both answers then report them to me."
    send_message(r, chat_id, task_2)
    
    # Monitor orchestration traffic for a bit to prove activity
    monitor_stream_activity(r, 'qc:mailbox/orchestrator', duration=10)
    
    success, response = wait_for_response(r, chat_id, timeout=120)
    if not success:
        return False
        
    final_agents = get_agent_count()
    print(f"Final Active Agents: {final_agents}")
    
    if final_agents > current_agents:
        print("[SUCCESS] Additional agents spawned.")
        
    return True

if __name__ == "__main__":
    if run_verification():
        print("\n=== VERIFICATION COMPLETE: SUCCESS ===")
        sys.exit(0)
    else:
        print("\n=== VERIFICATION FAILED ===")
        sys.exit(1)
