# Testing Guide

Complete guide for running end-to-end tests with live LLM API calls and monitoring system behavior.

## Quick Start

```bash
# 1. Start the Docker stack
docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime

# 2. Wait for services to be healthy
sleep 10

# 3. Send a test task
./scripts/send_test_task.sh
```

The orchestrator will receive your task, create a plan, potentially spawn agents, and return results. All communication happens via Redis Streams.

## Prerequisites

### Required
- Docker and Docker Compose
- `uv` for Python package management
- Valid `ANTHROPIC_API_KEY` in `.env` and `.env.docker`

### Environment Setup

```bash
# Copy sample files
cp .env.sample .env
cp .env.docker.sample .env.docker

# Edit both files and add your API key
# At minimum: ANTHROPIC_API_KEY=sk-ant-...
```

**Important:** The `.env` file in the root is automatically used by all commands. No manual export needed.

## Test Architecture

### Philosophy

Tests simulate real user behavior by:
1. Sending tasks to Redis (like a user would)
2. Monitoring Redis streams for responses
3. Observing agent spawning and delegation
4. Validating results

This is **more realistic and reliable** than trying to control the orchestrator from outside.

### Message Flow

```
Human → qc:mailbox/orchestrator (task)
       ↓
Orchestrator processes, may spawn agents
       ↓
Orchestrator → qc:mailbox/agent-* (delegate)
       ↓
Agent → qc:mailbox/orchestrator (results)
       ↓
Orchestrator → qc:mailbox/human (response)
```

## Running Tests

### Manual Testing (Recommended for Development)

Send a predefined test task:
```bash
./scripts/send_test_task.sh
```

Send a custom task:
```bash
./scripts/send_test_task.sh "Build a Flask REST API with user authentication"
```

Expected output:
```
✓ Sent task to orchestrator
→ Monitoring qc:mailbox/human for response...

[15s] ✓ Response #1
  From: orchestrator
  Message: I'll create a plan...

[78s] ✓ Response #2
  From: orchestrator
  Message: I've implemented the solution...

✓ Task appears complete after 78s
```

### Automated Test Suite

Start test-runner:
```bash
docker compose up -d test-runner
```

Run all realistic workflow tests:
```bash
docker compose exec test-runner uv run pytest tests/e2e_advanced/test_realistic_workflow.py -v -s
```

Run specific test:
```bash
docker compose exec test-runner uv run pytest tests/e2e_advanced/test_realistic_workflow.py::test_simple_task_without_spawning -v -s
```

### Test Cases

#### 1. Multi-Agent Parallel Work (5-10 minutes)
Tests complex task requiring multiple agents working in parallel.

```bash
docker compose exec test-runner uv run pytest \
  tests/e2e_advanced/test_realistic_workflow.py::test_multi_agent_parallel_work -v -s
```

**Validates:**
- Orchestrator creates plan
- Agents spawned dynamically (if needed)
- Parallel task execution
- Result integration

#### 2. Simple Task (1-2 minutes)
Tests basic orchestrator functionality without agent spawning.

```bash
docker compose exec test-runner uv run pytest \
  tests/e2e_advanced/test_realistic_workflow.py::test_simple_task_without_spawning -v -s
```

**Validates:**
- Direct response to simple queries
- No unnecessary agent spawning
- Fast response times

#### 3. Orchestrator Delegation (2-3 minutes)
Tests delegation to existing agent-runtime.

```bash
docker compose exec test-runner uv run pytest \
  tests/e2e_advanced/test_realistic_workflow.py::test_orchestrator_delegation_to_existing_agent -v -s
```

**Validates:**
- Orchestrator delegates to agent
- Agent completes work
- Orchestrator reviews results

## Monitoring System Behavior

### Real-Time Stream Monitoring

Monitor human mailbox (responses):
```bash
docker compose exec -T redis redis-cli XREAD BLOCK 0 STREAMS qc:mailbox/human 0-0
```

Monitor orchestrator mailbox (incoming tasks):
```bash
docker compose exec -T redis redis-cli XREAD BLOCK 0 STREAMS qc:mailbox/orchestrator 0-0
```

Monitor all mailboxes:
```bash
# List all mailbox streams
docker compose exec -T redis redis-cli KEYS "qc:mailbox/*"

# Watch specific mailbox
docker compose exec -T redis redis-cli XREAD BLOCK 0 STREAMS qc:mailbox/agent-runtime 0-0
```

### Container Logs

Orchestrator logs (planning and delegation):
```bash
docker compose logs -f orchestrator-runtime
```

Agent logs (task execution):
```bash
docker compose logs -f agent-runtime
```

All services:
```bash
docker compose logs -f
```

### Stream Inspection

Get stream length:
```bash
docker compose exec -T redis redis-cli XLEN qc:mailbox/human
```

Get latest 10 messages:
```bash
docker compose exec -T redis redis-cli XREVRANGE qc:mailbox/human + - COUNT 10
```

Get messages after specific ID:
```bash
docker compose exec -T redis redis-cli XREAD STREAMS qc:mailbox/human 1763282238120-0
```

### Agent Registry

List all registered agents:
```bash
curl http://localhost:8090/agents | jq
```

Check agent health:
```bash
curl http://localhost:8090/agents/agent-runtime | jq
```

### Spawned Containers

List running agents:
```bash
docker ps | grep agent
```

Watch for new agents:
```bash
watch -n 2 'docker ps --format "table {{.Names}}\t{{.Status}}\t{{.CreatedAt}}" | grep agent'
```

## Test Development

### Writing New Tests

Template for realistic E2E test:

```python
@pytest.mark.e2e_advanced
def test_your_scenario(docker_stack, redis_client):
    """Test description."""
    
    # Get baseline
    baseline_human = redis_client.xrevrange("qc:mailbox/human", count=1)
    baseline_id = baseline_human[0][0] if baseline_human else "0-0"
    
    # Send task
    timestamp = datetime.now(timezone.utc).isoformat()
    payload = json.dumps({"supervisor": "human"})
    
    redis_client.xadd(
        "qc:mailbox/orchestrator",
        {
            "timestamp": timestamp,
            "sender": "human",
            "recipient": "orchestrator",
            "message": "Your task description",
            "payload": payload,
        }
    )
    
    # Monitor for completion
    timeout = 300  # 5 minutes
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
                    
                    # Validate response
                    assert "expected content" in message
                    return  # Test passed
                    
        time.sleep(2)
    
    pytest.fail(f"Timeout after {timeout}s")
```

### Best Practices

**DO:**
- ✅ Use realistic tasks that test actual system capabilities
- ✅ Monitor streams, don't try to control everything
- ✅ Use generous timeouts (real LLM calls take time)
- ✅ Validate observable behavior, not implementation details
- ✅ Clean Redis between tests (`redis_cli("FLUSHALL")`)

**DON'T:**
- ❌ Mock Redis or LLM calls (defeats the purpose)
- ❌ Force specific orchestrator behavior with hints
- ❌ Use short timeouts (< 2 minutes for complex tasks)
- ❌ Test implementation details instead of outcomes
- ❌ Try to control agent spawning directly

## Troubleshooting

### No Response / Test Timeout

**Check if orchestrator is running:**
```bash
docker compose ps orchestrator-runtime
# Should show: Up (healthy)
```

**Check orchestrator logs:**
```bash
docker compose logs orchestrator-runtime --tail=50
```

**Verify message was received:**
```bash
docker compose exec -T redis redis-cli XLEN qc:mailbox/orchestrator
# Should be > 0 if messages are queued
```

**Check Redis connectivity:**
```bash
docker compose exec orchestrator-runtime redis-cli -h redis PING
# Should return: PONG
```

### Agent Spawning Fails

**Verify Docker socket access:**
```bash
docker compose exec orchestrator-runtime docker ps
# Should list containers
```

**Check spawn scripts exist:**
```bash
docker compose exec orchestrator-runtime ls -la /app/scripts/agent-management/
# Should show spawn-agent.sh, delete-agent.sh, etc.
```

**Review orchestrator logs for errors:**
```bash
docker compose logs orchestrator-runtime | grep -i "spawn\|agent_management"
```

### Test Runs But No Agents Spawn

**This is often correct behavior!** The orchestrator only spawns agents when:
- Task complexity warrants parallel work
- Existing agent capacity is insufficient
- Task explicitly requires specialized capabilities

For simple tasks, orchestrator + agent-runtime is sufficient.

**To force spawning, make task more complex:**
```bash
./scripts/send_test_task.sh "Build 5 separate Flask microservices, each with its own database schema, API endpoints, and comprehensive test suite."
```

### Redis Connection Issues

**Check Redis health:**
```bash
docker compose ps redis
# Should show: Up (healthy)
```

**Test Redis from orchestrator:**
```bash
docker compose exec orchestrator-runtime redis-cli -h redis INFO server
```

**Check Redis logs:**
```bash
docker compose logs redis --tail=50
```

### Test-Runner Issues

**Rebuild test-runner:**
```bash
docker compose build test-runner
docker compose up -d test-runner
```

**Check test-runner has Redis access:**
```bash
docker compose exec test-runner redis-cli -h redis PING
```

**Verify Python packages:**
```bash
docker compose exec test-runner uv run python -c "import redis; print(redis.__version__)"
```

## Performance Expectations

### Typical Durations

| Task Type | Duration | LLM Calls | Agent Spawning |
|-----------|----------|-----------|----------------|
| Simple query | 30-60s | 1-2 | Rarely |
| Code function | 1-3 min | 3-5 | Sometimes |
| Multi-module project | 5-10 min | 10-20 | Often |
| Complex system | 10-30 min | 20-50 | Usually |

### Factors Affecting Performance

- **LLM API latency**: Claude responses vary 2-10 seconds
- **Task complexity**: More iterations = longer duration
- **Agent spawning**: Container startup adds 5-10 seconds
- **Context size**: Large contexts slow processing
- **Tool calls**: Each tool adds latency

### Optimization Tips

- Use `reply_to` hints for direct delegation
- Start with simple tasks to validate stack health
- Run tests with different chat IDs for parallelism
- Monitor Redis stream lengths to detect bottlenecks

## Test Fixtures

### Available Fixtures (conftest.py)

**`docker_stack`** - Brings up full Docker Compose stack
- Services: redis, redis-mcp, agent-registry, orchestrator-runtime, agent-runtime
- Flushes Redis for clean state
- Yields when all services healthy

**`redis_client`** - Direct Redis client
- Connected to Docker stack Redis
- decode_responses=True for string handling
- Session scope, reused across tests

**`test_log_dir`** - Timestamped log directory
- Created for each test
- Path: `tests/logs/test_name_YYYYMMDD-HHMMSS/`

**`metrics_collector`** - MetricsCollector instance
- Tracks test metrics
- Auto-configured with test name and run ID

**`stream_baselines`** - Baseline stream IDs
- supervisor, orchestrator, agent_runtime, context_metrics, autonomous_events
- Use to filter out pre-existing messages

**`test_config`** - Configuration dict
- Empty dict for backwards compatibility
- Use for test-specific configuration

## CI/CD Integration

### GitHub Actions Example

```yaml
name: E2E Tests
on: [push, pull_request]

jobs:
  e2e:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Start Docker stack
        run: |
          cp .env.sample .env
          echo "ANTHROPIC_API_KEY=${{ secrets.ANTHROPIC_API_KEY }}" >> .env
          cp .env.docker.sample .env.docker
          echo "ANTHROPIC_API_KEY=${{ secrets.ANTHROPIC_API_KEY }}" >> .env.docker
          docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime
          sleep 30
      
      - name: Run E2E tests
        run: |
          docker compose exec -T test-runner uv run pytest tests/e2e_advanced/test_realistic_workflow.py -v
      
      - name: Capture logs on failure
        if: failure()
        run: |
          docker compose logs > test-logs.txt
      
      - uses: actions/upload-artifact@v3
        if: failure()
        with:
          name: test-logs
          path: test-logs.txt
```

### Environment Variables

**E2E_ADVANCED_TIMEOUT_MULTIPLIER** - Scale timeouts for slow environments
```bash
export E2E_ADVANCED_TIMEOUT_MULTIPLIER=2.0
```

**E2E_REUSE_STACK** - Reuse existing Docker stack between tests
```bash
export E2E_REUSE_STACK=1
```

**REDIS_HOST** - Override Redis hostname
```bash
export REDIS_HOST=your-redis-host
```

## Advanced Usage

### Running from Python

```python
from tests.e2e_advanced.send_task import send_task

# Send task and wait for completion
success = send_task(
    "Build a Flask REST API",
    timeout=600  # 10 minutes
)
```

### Custom Task Sender

```python
import redis
import json
from datetime import datetime, timezone

client = redis.Redis(host="redis", port=6379, decode_responses=True)

# Send task
client.xadd("qc:mailbox/orchestrator", {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "sender": "human",
    "recipient": "orchestrator",
    "message": "Your custom task",
    "payload": json.dumps({
        "supervisor": "human",
        "chat_id": "custom-test-123",
        "reply_to": "agent-runtime"  # Optional delegation hint
    })
})

# Monitor for response
baseline = "0-0"
while True:
    msgs = client.xread({"qc:mailbox/human": baseline}, count=10, block=2000)
    if msgs:
        for stream, entries in msgs:
            for entry_id, fields in entries:
                print(f"Response: {fields.get('message')}")
                baseline = entry_id
```

### Monitoring with Python

```python
import redis
import json

client = redis.Redis(host="redis", port=6379, decode_responses=True)

# Watch for agent spawning
baseline_orch = "0-0"
while True:
    msgs = client.xread({"qc:mailbox/orchestrator": baseline_orch}, count=10, block=2000)
    
    for stream, entries in msgs:
        for entry_id, fields in entries:
            payload = json.loads(fields.get("payload", "{}"))
            messages = payload.get("messages", [])
            
            for msg in messages:
                if msg.get("type") == "tool":
                    tool_name = msg.get("data", {}).get("name")
                    if tool_name == "agent_management":
                        print(f"Agent management call detected!")
            
            baseline_orch = entry_id
```

## Debugging Tips

### Enable Debug Logging

```bash
# Set log level to DEBUG
export QUADRACODE_LOG_LEVEL=DEBUG

# Restart services
docker compose restart orchestrator-runtime agent-runtime
```

### Capture Full Message Traces

```python
# In test, save full payload
for stream, entries in messages:
    for entry_id, fields in entries:
        with open("message_trace.json", "a") as f:
            json.dump({
                "entry_id": entry_id,
                "fields": fields,
                "timestamp": datetime.now().isoformat()
            }, f)
            f.write("\n")
```

### Interactive Debugging

```bash
# Start test-runner with shell
docker compose run --rm test-runner bash

# Inside container
cd /app
redis-cli -h redis PING
python tests/e2e_advanced/send_task.py
```

## Summary

**Key Points:**
1. Tests simulate real user behavior via Redis Streams
2. Orchestrator can spawn agents (has Docker socket)
3. Monitor streams to observe system behavior
4. Use generous timeouts for real LLM calls
5. Validate outcomes, not implementation details

**Quick Commands:**
```bash
# Manual test
./scripts/send_test_task.sh

# Automated tests
docker compose exec test-runner uv run pytest tests/e2e_advanced/test_realistic_workflow.py -v

# Monitor responses
docker compose exec -T redis redis-cli XREAD BLOCK 0 STREAMS qc:mailbox/human 0-0

# Check logs
docker compose logs -f orchestrator-runtime
```

For more details, see `README.md`, `IMPLEMENTATION.md`, and `TECHNICAL_IMPLEMENTATION.md`.

