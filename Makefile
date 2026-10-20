.PHONY: test lint typecheck check
test:
	uv run pytest
lint:
	uv run ruff check . && uv run ruff format --check .
typecheck:
	uv run pyright
check: lint typecheck test
