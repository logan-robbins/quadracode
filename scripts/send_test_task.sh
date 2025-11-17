#!/usr/bin/env bash
# Send a test task to the orchestrator
# Usage: ./scripts/send_test_task.sh [task_description]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Check if stack is running
if ! docker compose ps orchestrator-runtime | grep -q "Up"; then
    echo "❌ orchestrator-runtime not running"
    echo "   Start: docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime"
    exit 1
fi

# Default task if none provided
DEFAULT_TASK="Build a system that calculates derivatives of stock prices at 5s, 30s, and 2m intervals.

Requirements:
- Create 3 separate Python modules (derivative_5s.py, derivative_30s.py, derivative_2m.py)
- Each module should have a calculate_derivative(prices: list[float]) -> list[float] function
- Use numpy for numerical differentiation
- Include unit tests for each module
- Add a main.py that demonstrates all three

Please create a plan, spawn agents if needed to work in parallel, and implement the solution."

TASK="${1:-$DEFAULT_TASK}"

# Send task
docker compose exec test-runner python -c "
import sys
sys.path.insert(0, '/app/tests/e2e_advanced')
from send_task import send_task
task = '''$TASK'''
message_id = send_task(task)
print(f'Task: {task[:100]}...')
print(f'✓ Sent to qc:mailbox/orchestrator (ID: {message_id})')
print()
print('Watch message flow:')
print('  ./scripts/tail_streams.sh')
"

