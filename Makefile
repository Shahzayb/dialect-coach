.PHONY: setup up down

setup:
	python3 scripts/setup.py

up:
	docker compose up --build

down:
	docker compose down
