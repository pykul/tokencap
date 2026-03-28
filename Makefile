.PHONY: lint test test-live build publish

lint:
	ruff check tokencap tests
	mypy --strict tokencap

test:
	python3 -m pytest tests/unit tests/integration -v

test-live:
	python3 -m pytest tests/live -v

build:
	python3 -m build

publish:
	python3 -m twine upload dist/*
