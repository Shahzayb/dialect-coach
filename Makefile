.PHONY: setup up down test lint format

setup:
	python3 scripts/setup.py

up:
	docker compose up --build

down:
	docker compose down

# Runs offline: tests/conftest.py forces OFFLINE_MODE and clears the API keys, so this
# never touches the network. Needs `make up` (or `docker compose build`) first after any
# requirements.txt change, otherwise the image has no pytest.
test:
	docker compose run --rm app python -m pytest -q

# Ruff, configured in pyproject.toml. Run in the container so they use the pinned binary
# from requirements.txt — the same one CI runs, so a green `make lint` means a green CI.
lint:
	docker compose run --rm app ruff format --check .
	docker compose run --rm app ruff check .

format:
	docker compose run --rm app ruff format .
	docker compose run --rm app ruff check --fix .
