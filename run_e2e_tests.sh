#!/bin/bash

# E2E Test Runner with Debug Logging
set -e

echo "==================================="
echo "Starting E2E Test Suite Execution"
echo "==================================="

# Ensure stack is running
echo "Ensuring Docker stack is running..."
docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime

# Wait for services to be ready
echo "Waiting for services to be healthy..."
sleep 10

# Set environment variables
export E2E_REUSE_STACK=1
export PYTHONDONTWRITEBYTECODE=1

# Create logs directory
mkdir -p logs/e2e_advanced

# Test modules to run
TEST_MODULES=(
    "test_foundation_smoke"
    "test_foundation_long_run"
    "test_context_engine_stress"
    "test_fleet_management"
    "test_observability"
    "test_prp_autonomous"
    "test_workspace_integrity"
)

# Run each test module
for module in "${TEST_MODULES[@]}"; do
    echo ""
    echo "==================================="
    echo "Running: $module"
    echo "==================================="
    
    LOG_FILE="logs/e2e_advanced/${module}_$(date +%Y%m%d_%H%M%S).log"
    
    # Run the test
    if uv run pytest "tests/e2e_advanced/${module}.py" \
        -vv \
        --log-cli-level=INFO \
        --log-file="$LOG_FILE" \
        --log-file-level=DEBUG \
        --tb=short; then
        echo "✅ $module PASSED"
    else
        echo "❌ $module FAILED (see $LOG_FILE for details)"
    fi
done

echo ""
echo "==================================="
echo "E2E Test Suite Execution Complete"
echo "==================================="
