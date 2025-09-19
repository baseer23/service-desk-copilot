SHELL := /bin/bash

.PHONY: slm dev

slm:
	@bash scripts/start_slm.sh

dev:
	@echo "Starting backend on :8000 (frontend: run 'npm run dev' in ./frontend)"
	uvicorn backend.app.main:app --reload --port 8000

