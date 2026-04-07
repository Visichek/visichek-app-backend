#!/usr/bin/env bash
# =============================================================================
# Visichek Backend — Test Runner Script
# =============================================================================
# Usage:
#   ./scripts/run_tests.sh unit          Run unit tests only (no external deps)
#   ./scripts/run_tests.sh integration   Run integration tests (needs MongoDB + Redis)
#   ./scripts/run_tests.sh load          Run load tests with Locust (needs running server)
#   ./scripts/run_tests.sh all           Run unit + integration tests
#   ./scripts/run_tests.sh               Same as "unit" (default)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

MODE="${1:-unit}"

echo -e "${CYAN}============================================${NC}"
echo -e "${CYAN}  Visichek Backend Test Runner${NC}"
echo -e "${CYAN}  Mode: ${YELLOW}${MODE}${NC}"
echo -e "${CYAN}============================================${NC}"
echo

# Check dependencies
check_deps() {
    local missing=()
    python3 -c "import pytest" 2>/dev/null || missing+=("pytest")
    python3 -c "import pytest_asyncio" 2>/dev/null || missing+=("pytest-asyncio")
    python3 -c "import httpx" 2>/dev/null || missing+=("httpx")

    if [ ${#missing[@]} -gt 0 ]; then
        echo -e "${YELLOW}Installing missing test dependencies: ${missing[*]}${NC}"
        pip install "${missing[@]}" --break-system-packages -q
    fi
}

run_unit() {
    echo -e "${GREEN}Running unit tests...${NC}"
    echo -e "  These tests use mocks — no external services needed.\n"
    python3 -m pytest tests/unit/ tests/test_permissions.py tests/test_queue_registry.py \
        tests/test_response_envelope.py tests/test_role_config.py \
        -m "unit or not integration" \
        -v --tb=short --no-header \
        "$@"
}

run_integration() {
    echo -e "${GREEN}Running integration tests...${NC}"
    echo -e "  ${YELLOW}Requires: MongoDB on localhost:27017, Redis on localhost:6379${NC}\n"

    # Verify MongoDB is reachable
    if ! python3 -c "
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
async def check():
    c = AsyncIOMotorClient('mongodb://localhost:27017', serverSelectionTimeoutMS=2000)
    await c.admin.command('ping')
asyncio.run(check())
" 2>/dev/null; then
        echo -e "${RED}ERROR: MongoDB is not running on localhost:27017${NC}"
        echo "  Start it with: mongod --dbpath /tmp/mongo_test"
        echo "  Or via Docker: docker run -d -p 27017:27017 mongo:7"
        exit 1
    fi

    # Verify Redis is reachable
    if ! python3 -c "
import redis
r = redis.Redis(host='localhost', port=6379, socket_connect_timeout=2)
r.ping()
" 2>/dev/null; then
        echo -e "${RED}ERROR: Redis is not running on localhost:6379${NC}"
        echo "  Start it with: redis-server"
        echo "  Or via Docker: docker run -d -p 6379:6379 redis:7"
        exit 1
    fi

    python3 -m pytest tests/integration/ \
        -m "integration" \
        -v --tb=short --no-header \
        "$@"
}

run_load() {
    echo -e "${GREEN}Setting up load tests with Locust...${NC}"
    echo -e "  ${YELLOW}Requires: Running Visichek server on localhost:8000${NC}\n"

    python3 -c "import locust" 2>/dev/null || {
        echo -e "${YELLOW}Installing locust...${NC}"
        pip install locust --break-system-packages -q
    }

    # Check if server is running
    if ! python3 -c "
import httpx, sys
try:
    r = httpx.get('http://localhost:8000/health', timeout=3)
    sys.exit(0)
except:
    sys.exit(1)
" 2>/dev/null; then
        echo -e "${RED}ERROR: Visichek server is not running on localhost:8000${NC}"
        echo "  Start it with: fasterapi run-d"
        exit 1
    fi

    echo -e "${CYAN}Step 1: Seed test data${NC}"
    python3 tests/load/setup_load_test_data.py

    echo -e "\n${CYAN}Step 2: Launch Locust${NC}"
    echo -e "  Web UI: ${GREEN}http://localhost:8089${NC}"
    echo -e "  Or headless: locust -f tests/load/locustfile.py --headless -u 50 -r 5 -t 60s\n"

    cd tests/load
    locust -f locustfile.py --host=http://localhost:8000
}

# Main
check_deps

case "$MODE" in
    unit)
        run_unit "${@:2}"
        ;;
    integration)
        run_integration "${@:2}"
        ;;
    load)
        run_load "${@:2}"
        ;;
    all)
        run_unit "${@:2}"
        echo
        run_integration "${@:2}"
        ;;
    *)
        echo -e "${RED}Unknown mode: $MODE${NC}"
        echo "Usage: $0 {unit|integration|load|all}"
        exit 1
        ;;
esac

echo -e "\n${GREEN}Done!${NC}"
