#!/bin/bash
#
# AI-Optimized Test Runner for Quadracode
# This script runs tests in a container with verbose, AI-parseable output
#
set -e

# Colors for clear output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Configuration
TEST_MODE="${TEST_MODE:-smoke}"  # 'smoke' for quick tests, 'full' for comprehensive
REBUILD="${REBUILD:-false}"      # Set to 'true' to force container rebuild

echo -e "${BLUE}${BOLD}════════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}${BOLD}       QUADRACODE AI TEST AUTOMATION SYSTEM                      ${NC}"
echo -e "${BLUE}${BOLD}════════════════════════════════════════════════════════════════${NC}"
echo ""

# Function to log with timestamp
log() {
    echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Function to check exit status
check_status() {
    if [ $? -eq 0 ]; then
        log "${GREEN}✅ $1 succeeded${NC}"
    else
        log "${RED}❌ $1 failed${NC}"
        exit 1
    fi
}

# Step 1: Check Docker
log "${YELLOW}📋 STEP 1: Checking Docker...${NC}"
docker version > /dev/null 2>&1
check_status "Docker check"

# Step 2: Check API keys
log "${YELLOW}📋 STEP 2: Checking API keys...${NC}"
if [[ -z "${ANTHROPIC_API_KEY}" ]]; then
    # Try to load from .env file
    if [ -f .env ]; then
        export $(grep ANTHROPIC_API_KEY .env | xargs)
    fi
fi

if [[ "${ANTHROPIC_API_KEY}" == sk-* ]]; then
    log "${GREEN}✅ ANTHROPIC_API_KEY is set${NC}"
else
    log "${RED}❌ ANTHROPIC_API_KEY not set or invalid${NC}"
    log "${YELLOW}   Set it in .env or export it before running${NC}"
    exit 1
fi

# Step 3: Start core services
log "${YELLOW}📋 STEP 3: Starting core services...${NC}"
docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime 2>&1 | \
    while IFS= read -r line; do
        echo "   $line"
    done
check_status "Service startup"

# Wait for services to be healthy
log "${YELLOW}⏳ Waiting for services to be healthy...${NC}"
sleep 10

# Check service health
RUNNING_COUNT=$(docker compose ps --services --filter "status=running" | wc -l | tr -d ' ')
if [ "$RUNNING_COUNT" -ge 5 ]; then
    log "${GREEN}✅ $RUNNING_COUNT services running${NC}"
else
    log "${RED}❌ Only $RUNNING_COUNT services running (expected 5+)${NC}"
    docker compose ps
    exit 1
fi

# Step 4: Build test runner container if needed
log "${YELLOW}📋 STEP 4: Preparing test runner container...${NC}"
if [ "$REBUILD" = "true" ] || ! docker images | grep -q "quadracode-test-runner"; then
    log "Building test runner container..."
    docker build -f tests/Dockerfile.test-runner -t quadracode-test-runner . 2>&1 | \
        while IFS= read -r line; do
            echo "   $line"
        done
    check_status "Container build"
else
    log "${GREEN}✅ Using existing test runner image${NC}"
fi

# Step 5: Run tests in container with live output
log "${YELLOW}📋 STEP 5: Running tests (mode: ${TEST_MODE})...${NC}"
echo ""
echo -e "${BOLD}════════════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}                    TEST EXECUTION OUTPUT                         ${NC}"
echo -e "${BOLD}════════════════════════════════════════════════════════════════${NC}"
echo ""

# Run the test container with:
# - Network access to reach Redis and other services
# - Environment variables passed through
# - Live output streaming
# - Auto-remove after completion
docker run \
    --rm \
    --network="host" \
    -e ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
    -e TEST_MODE="${TEST_MODE}" \
    -e REDIS_HOST="localhost" \
    -e REDIS_PORT="6379" \
    -e AGENT_REGISTRY_URL="http://localhost:8090" \
    -e E2E_TURN_COOLDOWN_SECONDS="${E2E_TURN_COOLDOWN_SECONDS:-0.5}" \
    -e E2E_ADVANCED_TIMEOUT_MULTIPLIER="${E2E_ADVANCED_TIMEOUT_MULTIPLIER:-1.0}" \
    -v "$(pwd)/tests:/app/tests:ro" \
    -v "$(pwd)/quadracode-runtime:/app/quadracode-runtime:ro" \
    -v "$(pwd)/quadracode-contracts:/app/quadracode-contracts:ro" \
    -v "$(pwd)/quadracode-tools:/app/quadracode-tools:ro" \
    --name quadracode-test-runner \
    quadracode-test-runner

TEST_EXIT_CODE=$?

echo ""
echo -e "${BOLD}════════════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}                    TEST EXECUTION COMPLETE                       ${NC}"
echo -e "${BOLD}════════════════════════════════════════════════════════════════${NC}"
echo ""

# Step 6: Report final status
if [ $TEST_EXIT_CODE -eq 0 ]; then
    log "${GREEN}${BOLD}✅ ALL TESTS PASSED!${NC}"
else
    log "${RED}${BOLD}❌ SOME TESTS FAILED (exit code: $TEST_EXIT_CODE)${NC}"
fi

# Optional: Show container logs if tests failed
if [ $TEST_EXIT_CODE -ne 0 ]; then
    log "${YELLOW}📋 Recent orchestrator logs:${NC}"
    docker compose logs --tail=20 orchestrator-runtime
fi

exit $TEST_EXIT_CODE
