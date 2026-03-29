PYTHON ?= python3

.PHONY: lint test test-live build publish redis-up redis-down

lint:
	ruff check tokencap tests
	mypy --strict tokencap

test:
	$(PYTHON) -m pytest tests/unit tests/integration -v

test-live:
	$(PYTHON) -m pytest tests/live -v

build:
	rm -rf dist/
	$(PYTHON) -m build

publish:
	$(PYTHON) -m build
	twine upload dist/*

redis-up:
	docker compose -f docker/docker-compose.redis.yml up -d

redis-down:
	docker compose -f docker/docker-compose.redis.yml down
