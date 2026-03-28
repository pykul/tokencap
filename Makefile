PYTHON ?= python3

.PHONY: lint test test-live build publish

lint:
	ruff check tokencap tests
	mypy --strict tokencap

test:
	$(PYTHON) -m pytest tests/unit tests/integration -v

test-live:
	$(PYTHON) -m pytest tests/live -v

build:
	$(PYTHON) -m build

publish:
	$(PYTHON) -m twine upload dist/*
