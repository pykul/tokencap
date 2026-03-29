PYTHON ?= python3

.PHONY: lint test test-live build publish release redis-up redis-down

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

## Release — usage: make release VERSION=X.Y.Z
release:
ifndef VERSION
	@echo "ERROR: VERSION is required. Usage: make release VERSION=X.Y.Z"
	@exit 1
endif
	@echo "Checking version matches pyproject.toml..."
	@CURRENT=$$(python -c 'import tomllib; f=open("pyproject.toml","rb"); d=tomllib.load(f); f.close(); print(d["project"]["version"])'); \
	if [ "$$CURRENT" != "$(VERSION)" ]; then \
		echo "ERROR: pyproject.toml version is $$CURRENT but you passed $(VERSION)."; \
		echo "Bump the version in pyproject.toml first, then run make release."; \
		exit 1; \
	fi
	@echo "Checking tag v$(VERSION) does not already exist..."
	@if git tag | grep -q "^v$(VERSION)$$"; then \
		echo "ERROR: tag v$(VERSION) already exists locally."; \
		exit 1; \
	fi
	@if git ls-remote --tags origin | grep -q "refs/tags/v$(VERSION)$$"; then \
		echo "ERROR: tag v$(VERSION) already exists on remote."; \
		exit 1; \
	fi
	@echo "Running pre-release checks..."
	$(MAKE) lint
	$(MAKE) test
	rm -rf dist/
	python -m build
	twine check dist/*
	@echo ""
	@echo "All checks passed. Creating and pushing tag v$(VERSION)..."
	git tag v$(VERSION)
	git push origin v$(VERSION)
	@echo ""
	@echo "Tag v$(VERSION) pushed. The publish GitHub Action is now running."
	@echo "Monitor at: https://github.com/pykul/tokencap/actions/workflows/publish.yml"
	@echo "PyPI: https://pypi.org/project/tokencap/"

redis-up:
	docker compose -f docker/docker-compose.redis.yml up -d

redis-down:
	docker compose -f docker/docker-compose.redis.yml down
