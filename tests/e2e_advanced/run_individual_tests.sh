#!/bin/bash

# Run individual E2E test modules with debugging support
# This script provides an easy way to run tests one at a time

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if we're in the right directory
if [[ ! -f "../../pyproject.toml" ]] && [[ ! -f "/app/pyproject.toml" ]]; then
    echo -e "${RED}Error: Must run from tests/e2e_advanced directory${NC}"
    exit 1
fi

# If running in container, adjust path
if [[ -f "/app/pyproject.toml" ]] && [[ "$PWD" != "/app/tests/e2e_advanced" ]]; then
    cd /app/tests/e2e_advanced
fi

# Function to print usage
usage() {
    echo "Usage: $0 [OPTIONS] <test_module|all>"
    echo ""
    echo "Run E2E test modules individually for debugging"
    echo ""
    echo "Test Modules:"
    echo "  foundation_smoke     - Infrastructure validation (2-5 min)"
    echo "  foundation_long_run  - Sustained message flows (5-10 min)"
    echo "  context_engine_stress - Context engineering (10-15 min)"
    echo "  prp_autonomous      - HumanClone cycles (15-20 min)"
    echo "  fleet_management    - Agent lifecycle (5-10 min)"
    echo "  workspace_integrity - Workspace isolation (10-15 min)"
    echo "  observability       - Logs and metrics (10-15 min)"
    echo "  all                 - Run all modules in sequence"
    echo ""
    echo "Options:"
    echo "  -v, --verbose       Enable verbose output"
    echo "  -d, --debug         Enable debug mode with pauses"
    echo "  -s, --stop-on-fail  Stop at first failure"
    echo "  -n, --no-capture    Don't capture output to files"
    echo "  -l, --list          List all test modules"
    echo "  -h, --help          Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 foundation_smoke           # Run smoke tests"
    echo "  $0 -v prp_autonomous         # Run PRP tests with verbose output"
    echo "  $0 --debug fleet_management  # Run with debug pauses"
    echo "  $0 all --stop-on-fail        # Run all, stop at first failure"
}

# Parse arguments
VERBOSE=""
DEBUG=""
STOP_ON_FAIL=""
NO_CAPTURE=""
LIST=""

while [[ $# -gt 0 ]]; do
    case $1 in
        -v|--verbose)
            VERBOSE="--verbose"
            shift
            ;;
        -d|--debug)
            DEBUG="--debug"
            echo -e "${YELLOW}Debug mode: Will pause between test functions${NC}"
            shift
            ;;
        -s|--stop-on-fail)
            STOP_ON_FAIL="--stop-on-failure"
            shift
            ;;
        -n|--no-capture)
            NO_CAPTURE="--no-capture"
            shift
            ;;
        -l|--list)
            LIST="--list"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            MODULE=$1
            shift
            ;;
    esac
done

# Check prerequisites
echo -e "${GREEN}Checking prerequisites...${NC}"

# Check Docker
if ! docker compose ps --services --filter "status=running" | grep -q "redis"; then
    echo -e "${YELLOW}Warning: Docker services may not be running${NC}"
    echo "Start them with: docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime"
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Check API key
if [[ -z "$ANTHROPIC_API_KEY" ]]; then
    # Try loading from .env files
    if [[ -f "/app/.env" ]]; then
        export $(grep ANTHROPIC_API_KEY /app/.env 2>/dev/null | xargs) 2>/dev/null || true
    elif [[ -f "../../.env" ]]; then
        export $(grep ANTHROPIC_API_KEY ../../.env 2>/dev/null | xargs) 2>/dev/null || true
    fi
    
    if [[ -z "$ANTHROPIC_API_KEY" ]]; then
        echo -e "${RED}Error: ANTHROPIC_API_KEY not set${NC}"
        echo "Set it in .env or .env.docker or export it"
        exit 1
    fi
fi

# Run the test runner
echo -e "${GREEN}Starting test runner...${NC}"

if [[ -n "$DEBUG" ]]; then
    # Debug mode: run with extra verbosity and pauses
    export PYTEST_CURRENT_TEST_PAUSE=1
    uv run python run_tests.py $MODULE --verbose $STOP_ON_FAIL $NO_CAPTURE
else
    # Normal mode
    uv run python run_tests.py $MODULE $VERBOSE $STOP_ON_FAIL $NO_CAPTURE $LIST
fi

# Check exit code
EXIT_CODE=$?

if [[ $EXIT_CODE -eq 0 ]]; then
    echo -e "${GREEN}✓ Tests completed successfully${NC}"
else
    echo -e "${RED}✗ Tests failed with exit code $EXIT_CODE${NC}"
fi

exit $EXIT_CODE
