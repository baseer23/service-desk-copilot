SHELL := /bin/bash

BACKEND_DIR := backend
FRONTEND_DIR := frontend

.PHONY: dev slm fmt lint type test security compose-up compose-down ingest-sample bench-air

dev:
	@bash scripts/dev.sh

slm:
	@bash scripts/start_slm.sh

fmt:
	ruff check --fix $(BACKEND_DIR)
	black $(BACKEND_DIR)
	cd $(FRONTEND_DIR) && npm run format

lint:
	ruff check $(BACKEND_DIR)
	cd $(FRONTEND_DIR) && npm run lint

type:
	mypy $(BACKEND_DIR)/app
	cd $(FRONTEND_DIR) && npm run typecheck

test:
	pytest $(BACKEND_DIR)/app/tests
	cd $(FRONTEND_DIR) && npm run test

security:
	safety check --full-report
	cd $(FRONTEND_DIR) && npm run audit

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
