
import json
import redis
import uuid
import time
import sys
from datetime import datetime, timezone

def test_orchestrator_ping():
    r = redis.Redis(host='localhost', port=6379, decode_responses=True)
    
    chat_id = f"verification-{uuid.uuid4()}"
    msg_content = "Ping! Are you there?"
    
    payload = {
        'chat_id': chat_id,
        'messages': [{
            'type': 'human',
            'data': {
                'content': msg_content,
                'type': 'human',
                'id': str(uuid.uuid4())
            }
        }]
    }
    
    envelope = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'sender': 'human',
        'recipient': 'orchestrator',
        'message': msg_content,
        'payload': json.dumps(payload)
    }
    
    print(f"Sending message to qc:mailbox/orchestrator with chat_id: {chat_id}")
    r.xadd('qc:mailbox/orchestrator', envelope)
    
    # Poll for response
    print("Waiting for response on qc:mailbox/human...")
    start_time = time.time()
    last_id = '$'
    
    while time.time() - start_time < 30:
        # Read new messages
        streams = r.xread({'qc:mailbox/human': last_id}, count=1, block=1000)
        
        if not streams:
            continue
            
        for stream_name, messages in streams:
            for message_id, data in messages:
                last_id = message_id
                
                # Check if this message belongs to our chat_id
                payload_str = data.get('payload')
                if payload_str:
                    try:
                        resp_payload = json.loads(payload_str)
                        if resp_payload.get('chat_id') == chat_id:
                            print(f"\nSUCCESS! Received response: {data.get('message')}")
                            print(f"Full payload: {resp_payload}")
                            return True
                    except json.JSONDecodeError:
                        pass
        
        print(".", end="", flush=True)

    print("\nTIMEOUT: No response received after 30 seconds.")
    return False

if __name__ == "__main__":
    success = test_orchestrator_ping()
    sys.exit(0 if success else 1)
