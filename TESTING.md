# Testing Guide

How to validate the Quadracode message fabric works: send tasks, watch Redis Streams.

## Quick Start

```bash
# Terminal 1: Start stack
docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime

# Terminal 2: Watch message flow
./scripts/tail_streams.sh

# Terminal 3: Send task
./scripts/send_test_task.sh
```

Watch Terminal 2 to see messages flow through the system.

## Purpose

Validate that Redis Streams work as the communication fabric:
- Messages sent to `qc:mailbox/orchestrator` are received
- Orchestrator processes and responds to `qc:mailbox/human`
- Logs show processing activity

**That's it. If you can see messages flowing, the system works.**

## Prerequisites

- Docker and Docker Compose running
- `ANTHROPIC_API_KEY` in `.env` and `.env.docker`

```bash
cp .env.sample .env
cp .env.docker.sample .env.docker
# Edit both files, add: ANTHROPIC_API_KEY=sk-ant-...
```

## Manual Testing

### Send a Task

```bash
./scripts/send_test_task.sh
```

Or custom task:
```bash
./scripts/send_test_task.sh "Your task here"
```

Output:
```
Task: Build a system that calculates derivatives...
✓ Sent to qc:mailbox/orchestrator (ID: 1763282238120-0)

Watch message flow:
  ./scripts/tail_streams.sh
```

### Watch Message Flow

```bash
./scripts/tail_streams.sh
```

Shows all mailbox streams in real-time with color coding:
- Purple: human mailbox
- Blue: orchestrator mailbox
- Green: agent mailboxes

### Watch Logs

```bash
# Orchestrator
docker compose logs -f orchestrator-runtime

# Agent
docker compose logs -f agent-runtime

# All
docker compose logs -f
```

## Monitoring Commands

### Redis Streams

```bash
# Watch human mailbox
docker compose exec -T redis redis-cli XREAD BLOCK 0 STREAMS qc:mailbox/human 0-0

# List all mailboxes
docker compose exec -T redis redis-cli KEYS "qc:mailbox/*"

# Get stream length
docker compose exec -T redis redis-cli XLEN qc:mailbox/orchestrator

# Get latest messages
docker compose exec -T redis redis-cli XREVRANGE qc:mailbox/human + - COUNT 10
```

### Agent Registry

```bash
# List agents
curl http://localhost:8090/agents | jq

# Check agent health
curl http://localhost:8090/agents/agent-runtime | jq
```

### Containers

```bash
# List all
docker ps

# Watch for spawned agents
watch -n 2 'docker ps | grep agent'
```

## Troubleshooting

### No Response

Check orchestrator is running:
```bash
docker compose ps orchestrator-runtime
```

Check logs:
```bash
docker compose logs orchestrator-runtime --tail=50
```

Verify Redis connection:
```bash
docker compose exec orchestrator-runtime redis-cli -h redis PING
```

### Message Stuck

Check if message queued:
```bash
docker compose exec -T redis redis-cli XLEN qc:mailbox/orchestrator
```

View queued messages:
```bash
docker compose exec -T redis redis-cli XREVRANGE qc:mailbox/orchestrator + - COUNT 10
```

### Agent Spawning Fails

Verify Docker socket access:
```bash
docker compose exec orchestrator-runtime docker ps
```

Check spawn scripts:
```bash
docker compose exec orchestrator-runtime ls -la /app/scripts/agent-management/
```

## What to Observe

### Success Looks Like

1. **Terminal running `tail_streams.sh` shows:**
   ```
   [timestamp] qc:mailbox/orchestrator ... human -> orchestrator: Build a system...
   [timestamp] qc:mailbox/human ... orchestrator -> human: I'll create a plan...
   ```

2. **Orchestrator logs show:**
   ```
   Processing message from human
   Invoking LLM driver
   Tool calls: [...]
   Response sent to human
   ```

3. **Redis shows messages:**
   ```bash
   $ docker compose exec -T redis redis-cli XLEN qc:mailbox/human
   1
   ```

### That's It

If you see these three things, the message fabric works. Everything else (agent spawning, task completion, etc.) is observable through the same streams and logs.

## Performance

- Simple task: 30-60s
- Complex task: 5-10 minutes
- Tasks are real LLM API calls (costs tokens)

## CI/CD

```yaml
- name: Test message flow
  run: |
    docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime
    sleep 30
    docker compose exec -T test-runner uv run pytest tests/e2e_advanced/test_realistic_workflow.py -v
```

## Environment Variables

```bash
# Increase test timeout for slow networks
export E2E_ADVANCED_TIMEOUT_MULTIPLIER=2.0

# Reuse stack between tests
export E2E_REUSE_STACK=1
```

## Summary

**Test purpose:** Validate Redis Streams message fabric works

**How:** 
1. Send message
2. Watch streams
3. Observe response

**Success:** Message goes orchestrator → (processing) → human

For architecture details, see `README.md` and `TECHNICAL_IMPLEMENTATION.md`.
