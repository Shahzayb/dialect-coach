.PHONY: setup up down test

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
