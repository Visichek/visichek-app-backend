.PHONY: install lint typecheck test test-unit test-integration test-load ci dev clean

# Install all dependencies
install:
	pip install -r requirements.txt -r requirements-dev.txt

# Run linting
lint:
	ruff check .
	ruff format --check .

# Run type checking
typecheck:
	mypy --ignore-missing-imports .

# Run unit tests (no external services needed)
test-unit:
	pytest tests/unit/ -v --tb=short

# Run integration tests (requires MongoDB + Redis)
# Start services first: docker-compose -f docker-compose.test.yml up -d
test-integration:
	pytest tests/integration/ -v --tb=short

# Run load tests
test-load:
	locust -f tests/load/locustfile.py --headless -u 50 -r 10 --run-time 60s

# Run all tests
test: test-unit test-integration

# Full CI check (same as CI pipeline)
ci: lint typecheck test

# Start development server
dev:
	uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Start test infrastructure
test-infra-up:
	docker-compose -f docker-compose.test.yml up -d

test-infra-down:
	docker-compose -f docker-compose.test.yml down

# Clean up
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache
