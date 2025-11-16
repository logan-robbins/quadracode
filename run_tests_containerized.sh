#!/bin/bash
#
# Simplified test runner that automatically uses .env file
# Usage: ./run_tests_containerized.sh [smoke|integration|full]
#
set -e

TEST_MODE="${1:-smoke}"

echo "============================================================"
echo "QUADRACODE CONTAINERIZED TEST RUNNER"
echo "============================================================"
echo "Test Mode: $TEST_MODE"
echo "Using .env file for configuration"
echo ""

# Check if .env exists
if [ ! -f ".env" ]; then
    echo "❌ ERROR: .env file not found in current directory"
    echo "   Please ensure you're in the repository root and .env exists"
    exit 1
fi

# Check if API key is in .env
if ! grep -q "ANTHROPIC_API_KEY=sk-" .env; then
    echo "⚠️  WARNING: ANTHROPIC_API_KEY may not be set correctly in .env"
    echo "   Tests requiring LLM calls may fail"
fi

# Build container if it doesn't exist
if ! docker images | grep -q "quadracode-test-runner"; then
    echo "📦 Building test container (first time only)..."
    docker build -f tests/Dockerfile.test-runner -t quadracode-test-runner .
    echo "✅ Container built successfully"
fi

# Check if services are running
RUNNING=$(docker compose ps --services --filter "status=running" 2>/dev/null | wc -l | tr -d ' ')
if [ "$RUNNING" -lt 5 ]; then
    echo "🔄 Starting required services..."
    docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime
    echo "⏳ Waiting for services to be healthy..."
    sleep 10
fi

echo "🚀 Running tests in container..."
echo ""

# Run tests with .env file mounted
docker run --rm \
    --network="host" \
    --env-file .env \
    -e TEST_MODE="$TEST_MODE" \
    -e REDIS_HOST="localhost" \
    -e AGENT_REGISTRY_URL="http://localhost:8090" \
    quadracode-test-runner

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "✅ ALL TESTS PASSED"
else
    echo ""
    echo "❌ SOME TESTS FAILED (exit code: $EXIT_CODE)"
fi

exit $EXIT_CODE
