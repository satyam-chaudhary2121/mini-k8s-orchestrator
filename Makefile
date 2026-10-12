.PHONY: install lint format typecheck test test-unit test-docker up down demo clean

install:
	pip install -e ".[dev]"

lint:
	ruff check . && black --check .

format:
	black . && ruff check . --fix

typecheck:
	mypy orchestrator contracts worker cli

test:
	pytest -m "not docker"

test-docker:
	pytest -m docker

up:
	docker compose up --build -d

down:
	docker compose down -v

demo:
	./scripts/demo.sh
