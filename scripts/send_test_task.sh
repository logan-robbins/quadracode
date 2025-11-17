#!/usr/bin/env bash
# Send a test task to the orchestrator and monitor responses
# Usage: ./scripts/send_test_task.sh [task_description]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

# Check if stack is running
if ! docker compose ps orchestrator-runtime | grep -q "Up"; then
    echo "❌ Error: orchestrator-runtime is not running"
    echo "   Start it with: docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime"
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

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Sending test task to orchestrator"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Execute the task sender from within the orchestrator container
docker compose exec orchestrator-runtime python -c "
import sys
sys.path.insert(0, '/app/tests/e2e_advanced')
from send_task import send_task

task = '''$TASK'''
success = send_task(task, timeout=600)
sys.exit(0 if success else 1)
"

EXIT_CODE=$?

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ $EXIT_CODE -eq 0 ]; then
    echo "✓ Test task completed successfully"
else
    echo "✗ Test task failed or timed out"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

exit $EXIT_CODE

