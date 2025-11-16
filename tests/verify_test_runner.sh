#!/bin/bash

# Verify test-runner container has everything needed for E2E tests

echo "Verifying test-runner container setup..."
echo "========================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to check status
check_status() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}✓${NC} $2"
    else
        echo -e "${RED}✗${NC} $2"
        return 1
    fi
}

# Start or rebuild test-runner if needed
echo "Building/starting test-runner container..."
docker compose up -d --build test-runner
sleep 3

echo ""
echo "Checking container environment:"
echo "--------------------------------"

# Check if container is running
docker compose ps test-runner | grep -q "running"
check_status $? "Container is running" || exit 1

# Check Python and uv
docker compose exec -T test-runner python --version > /dev/null 2>&1
check_status $? "Python is available"

docker compose exec -T test-runner uv --version > /dev/null 2>&1
check_status $? "uv is installed"

# Check virtual environment
docker compose exec -T test-runner test -d /app/.venv
check_status $? "Virtual environment exists at /app/.venv"

# Check if pytest is available
docker compose exec -T test-runner uv run pytest --version > /dev/null 2>&1
check_status $? "pytest is installed"

# Check test scripts
docker compose exec -T test-runner test -f /app/tests/e2e_advanced/run_tests.py
check_status $? "run_tests.py exists"

docker compose exec -T test-runner test -x /app/tests/e2e_advanced/run_individual_tests.sh
check_status $? "run_individual_tests.sh is executable"

# Check test modules
docker compose exec -T test-runner test -f /app/tests/e2e_advanced/test_foundation_smoke.py
check_status $? "Test modules are available"

echo ""
echo "Checking environment variables:"
echo "--------------------------------"

# Check critical environment variables
docker compose exec -T test-runner printenv | grep -q "ANTHROPIC_API_KEY"
check_status $? "ANTHROPIC_API_KEY is set"

docker compose exec -T test-runner printenv | grep -q "REDIS_HOST=redis"
check_status $? "REDIS_HOST is set to 'redis'"

docker compose exec -T test-runner printenv | grep -q "AGENT_REGISTRY_URL=http://agent-registry:8090"
check_status $? "AGENT_REGISTRY_URL uses Docker DNS"

docker compose exec -T test-runner printenv | grep -q "MCP_REDIS_SERVER_URL=http://redis-mcp:8000"
check_status $? "MCP_REDIS_SERVER_URL uses Docker DNS"

echo ""
echo "Checking network connectivity:"
echo "--------------------------------"

# Check Redis connectivity
docker compose exec -T test-runner python -c "import socket; socket.create_connection(('redis', 6379), timeout=2)" 2>/dev/null
check_status $? "Can connect to Redis"

# Check agent-registry connectivity
docker compose exec -T test-runner python -c "import socket; socket.create_connection(('agent-registry', 8090), timeout=2)" 2>/dev/null
check_status $? "Can connect to agent-registry"

# Check redis-mcp connectivity  
docker compose exec -T test-runner python -c "import socket; socket.create_connection(('redis-mcp', 8000), timeout=2)" 2>/dev/null
check_status $? "Can connect to redis-mcp"

echo ""
echo "Testing test runner:"
echo "--------------------"

# Try listing test modules
docker compose exec -T test-runner bash -c "cd /app/tests/e2e_advanced && python run_tests.py --list" > /dev/null 2>&1
check_status $? "Can list test modules"

echo ""
echo "========================================="
echo "Verification complete!"
echo ""
echo "To run tests in the container:"
echo "  docker compose exec test-runner bash -c 'cd /app/tests/e2e_advanced && ./run_individual_tests.sh foundation_smoke'"
echo ""
echo "Or interactively:"
echo "  docker compose exec -it test-runner bash"
echo "  cd /app/tests/e2e_advanced"
echo "  ./run_individual_tests.sh --list"
