SHELL := /bin/bash
PYTHON := .venv/bin/python
.PHONY: dev install test e2e fmt lint type

dev:
	bash scripts/dev.sh

install:
	$(PYTHON) -m pip install -r requirements.txt
	npm --prefix frontend ci
	$(PYTHON) -m pip check

test:
	$(PYTHON) -m pytest backend/app/tests -q
	npm --prefix frontend test

e2e:
	npm --prefix frontend run test:e2e

fmt:
	.venv/bin/black backend
	npm --prefix frontend run format

lint:
	.venv/bin/ruff check backend
	npm --prefix frontend run lint

type:
	.venv/bin/mypy backend/app
	npm --prefix frontend run typecheck
