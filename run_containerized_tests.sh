#!/bin/bash
#
# Simple script to run tests in a container with clear output for AI monitoring
#
set -e

# Configuration
TEST_MODE="${1:-smoke}"  # smoke, integration, or full
REBUILD="${REBUILD:-false}"

echo "============================================================"
echo "QUADRACODE CONTAINERIZED TEST RUNNER"
echo "============================================================"
echo "Test Mode: $TEST_MODE"
echo "Time: $(date)"
echo ""

# Step 1: Build container if needed
if [ "$REBUILD" = "true" ] || ! docker images | grep -q "quadracode-test-runner"; then
    echo "📦 Building test container..."
    docker build -f tests/Dockerfile.test-runner -t quadracode-test-runner . || exit 1
    echo "✅ Container built successfully"
else
    echo "✅ Using existing test container image"
fi

# Step 2: Ensure services are running
echo ""
echo "🔍 Checking Docker services..."
RUNNING=$(docker compose ps --services --filter "status=running" | wc -l | tr -d ' ')
if [ "$RUNNING" -lt 5 ]; then
    echo "⚠️  Only $RUNNING services running, starting required services..."
    docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime
    echo "⏳ Waiting for services to be healthy..."
    sleep 10
fi

# Step 3: Run tests in container
echo ""
echo "🚀 Starting test execution in container..."
echo "   Mode: $TEST_MODE"
echo "   Monitoring: docker logs -f quadracode-test-runner"
echo ""
echo "============================================================"
echo "TEST OUTPUT BEGINS"
echo "============================================================"
echo ""

# Run container with:
# - Host network (to reach Redis and services on localhost)
# - Environment variables
# - Auto-remove when done
# - Named for easy log tailing
docker run \
    --rm \
    --network="host" \
    --name quadracode-test-runner \
    -e ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
    -e TEST_MODE="${TEST_MODE}" \
    -e REDIS_HOST="localhost" \
    -e REDIS_PORT="6379" \
    -e AGENT_REGISTRY_URL="http://localhost:8090" \
    quadracode-test-runner

EXIT_CODE=$?

echo ""
echo "============================================================"
echo "TEST OUTPUT ENDS"
echo "============================================================"
echo ""

if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ ALL TESTS PASSED"
else
    echo "❌ SOME TESTS FAILED (exit code: $EXIT_CODE)"
fi

exit $EXIT_CODE
