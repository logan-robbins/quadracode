#!/bin/bash

# ==============================================================================
# E2E TEST RUNNER FOR AI AGENTS
# This script runs the full Quadracode E2E integration test suite
# with real LLM calls to Anthropic Claude
# ==============================================================================

set -e

echo "=============================================="
echo "QUADRACODE E2E TEST RUNNER FOR AI AGENTS"
echo "=============================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Step 1: Check Docker
echo "Step 1: Checking Docker..."
if docker version > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Docker is running${NC}"
else
    echo -e "${RED}✗ Docker is not running. Please start Docker Desktop/Engine.${NC}"
    exit 1
fi

# Step 2: Check API keys
echo ""
echo "Step 2: Checking API keys..."
if grep -q "ANTHROPIC_API_KEY=sk-" .env 2>/dev/null && grep -q "ANTHROPIC_API_KEY=sk-" .env.docker 2>/dev/null; then
    echo -e "${GREEN}✓ API keys are configured${NC}"
else
    echo -e "${YELLOW}⚠ API keys may not be set properly${NC}"
    echo "Ensure ANTHROPIC_API_KEY is set in both .env and .env.docker"
    echo "Example: ANTHROPIC_API_KEY=sk-ant-api03-..."
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Step 3: Start Docker stack
echo ""
echo "Step 3: Starting Docker services..."
echo "This will start: redis, redis-mcp, agent-registry, orchestrator-runtime, agent-runtime, test-runner"
docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime test-runner

# Step 4: Wait for services
echo ""
echo "Step 4: Waiting for services to be healthy (30 seconds)..."
sleep 30

# Step 5: Verify services
echo ""
echo "Step 5: Verifying all services are running..."
RUNNING_COUNT=$(docker compose ps --services --filter "status=running" | wc -l | tr -d ' ')
if [ "$RUNNING_COUNT" -eq "6" ]; then
    echo -e "${GREEN}✓ All 6 services are running${NC}"
else
    echo -e "${YELLOW}⚠ Only $RUNNING_COUNT/6 services are running${NC}"
    docker compose ps
    echo "Waiting 20 more seconds..."
    sleep 20
fi

# Step 6: Test Redis connectivity
echo ""
echo "Step 6: Testing Redis connectivity..."
if docker compose exec -T redis redis-cli PING | grep -q "PONG"; then
    echo -e "${GREEN}✓ Redis is responding${NC}"
else
    echo -e "${RED}✗ Redis is not responding${NC}"
    exit 1
fi

# Step 7: Choose test mode
echo ""
echo "Step 7: Choose test mode:"
echo "  1) Quick smoke test (2-5 minutes, no LLM calls)"
echo "  2) Run individual test module (5-20 minutes each) - RECOMMENDED"
echo "  3) List all available test modules"
echo ""
read -p "Enter choice (1-3): " choice

case $choice in
    1)
        echo ""
        echo -e "${GREEN}Running smoke tests...${NC}"
        docker compose exec test-runner bash -c "cd /app && uv run pytest tests/e2e_advanced/test_foundation_smoke.py -v"
        ;;
    2)
        echo ""
        echo "Available test modules:"
        echo "  1) foundation_long_run - Message flows (10 min)"
        echo "  2) context_engine_stress - Context management (15 min)"
        echo "  3) prp_autonomous - HumanClone cycles (20 min)"
        echo "  4) fleet_management - Agent spawning (10 min)"
        echo "  5) workspace_integrity - Workspace isolation (15 min)"
        echo "  6) observability - Logging/metrics (15 min)"
        read -p "Enter module number (1-6): " module_choice
        
        MODULE_NAME=""
        case $module_choice in
            1) MODULE_NAME="foundation_long_run" ;;
            2) MODULE_NAME="context_engine_stress" ;;
            3) MODULE_NAME="prp_autonomous" ;;
            4) MODULE_NAME="fleet_management" ;;
            5) MODULE_NAME="workspace_integrity" ;;
            6) MODULE_NAME="observability" ;;
            *) echo "Invalid choice"; exit 1 ;;
        esac
        
        echo -e "${GREEN}Running $MODULE_NAME test...${NC}"
        docker compose exec test-runner bash -c "cd /app/tests/e2e_advanced && ./run_individual_tests.sh $MODULE_NAME"
        ;;
    3)
        echo ""
        echo -e "${GREEN}Available test modules:${NC}"
        docker compose exec test-runner bash -c "cd /app/tests/e2e_advanced && ./run_individual_tests.sh --list"
        echo ""
        echo "To run a specific module, restart this script and choose option 2"
        exit 0
        ;;
    *)
        echo "Invalid choice"
        exit 1
        ;;
esac

# Capture exit code
TEST_EXIT_CODE=$?

# Step 8: Report results
echo ""
echo "=============================================="
if [ $TEST_EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}✓ TESTS PASSED SUCCESSFULLY${NC}"
else
    echo -e "${RED}✗ TESTS FAILED WITH EXIT CODE: $TEST_EXIT_CODE${NC}"
fi
echo "=============================================="

# Show logs location
echo ""
echo "Test logs available at:"
echo "  ./tests/e2e_advanced/logs/"
echo ""
echo "To view container logs:"
echo "  docker compose logs orchestrator-runtime"
echo "  docker compose logs agent-runtime"

exit $TEST_EXIT_CODE
