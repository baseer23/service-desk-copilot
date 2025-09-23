SHELL := /bin/bash

.PHONY: dev slm fmt test compose-up compose-down ingest-sample bench-air

dev:
	@bash scripts/dev.sh

slm:
	@bash scripts/start_slm.sh

fmt:
	ruff check --fix backend
	black backend
	cd frontend && npx prettier -w .

test:
	pytest backend/app/tests

compose-up:
	docker compose up -d neo4j

compose-down:
	docker compose down

ingest-sample:
	curl -s -X POST http://localhost:8000/ingest/paste \
	  -H "Content-Type: application/json" \
	  -d '{"title":"Sample Manual","text":"Widgets 101. A widget has parts A, B, and C. Part A connects to Part B. Safety requires A before B."}'

bench-air:
	@python scripts/mac_air_check.py
