.PHONY: setup up down app test lint format typecheck check

setup:
	python3 scripts/setup.py

up:
	docker compose up --build

down:
	docker compose down

# Installs a "Dialect Coach" icon in ~/Applications (Spotlight, Launchpad, drag to the Dock).
# One click starts the app and opens the browser; the next click stops it. It points at this
# checkout by absolute path, so re-run this if the repo moves.
APP := $(HOME)/Applications/Dialect Coach.app
app:
	rm -rf "$(APP)"
	osacompile -o "$(APP)" -e 'do shell script "/usr/bin/python3 " & quoted form of "$(CURDIR)/scripts/launcher.py"'
	@echo "Installed $(APP)"

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

typecheck:
	docker compose run --rm app mypy

# Everything CI runs, in the order CI runs it. A green `make check` is a green CI run.
check: lint typecheck test
