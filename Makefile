SHELL := /bin/bash
PYTHON := .venv/bin/python
.PHONY: dev install test test-fast test-memory test-mcp test-ui test-regression test-live e2e fmt lint type

OFFLINE_ENV := DESKMATE_TEST_PROFILE=offline DESKMATE_ALLOW_LIVE_API_TESTS=false TEST_MODE=true GENERATION_PROVIDER=stub EMBED_PROVIDER=stub SPEECH_ENABLED=false NEO4J_URI= MCP_SERVERS='[]'

dev:
	bash scripts/dev.sh

install:
	$(PYTHON) -m pip install -r requirements.txt
	npm --prefix frontend ci
	$(PYTHON) -m pip check

test:
	$(MAKE) test-fast

test-fast:
	$(OFFLINE_ENV) $(PYTHON) scripts/select_tests.py --run

test-memory:
	$(OFFLINE_ENV) $(PYTHON) -m pytest -q backend/app/tests/test_semantic_memory.py backend/app/tests/test_general_conversations.py
	npm --prefix frontend test -- --run src/App.test.tsx

test-mcp:
	$(OFFLINE_ENV) $(PYTHON) -m pytest -q backend/app/tests/test_mcp.py backend/app/tests/test_mcp_security.py backend/app/tests/test_multi_mcp.py

test-ui:
	npm --prefix frontend test

test-regression:
	$(OFFLINE_ENV) $(PYTHON) -m pytest backend/app/tests -q -m "not live"
	npm --prefix frontend test
	npm --prefix frontend run typecheck
	npm --prefix frontend run build
	$(OFFLINE_ENV) npm --prefix frontend run test:e2e

test-live:
	@test "$(DESKMATE_ALLOW_LIVE_API_TESTS)" = "true" || (echo "Refusing live tests: set DESKMATE_ALLOW_LIVE_API_TESTS=true after authorization."; exit 2)
	@test "$(DESKMATE_TEST_PROFILE)" = "live" || (echo "Refusing live tests: set DESKMATE_TEST_PROFILE=live."; exit 2)
	@echo "Authorized live services selected by markers: Neo4j Aura only; model, Speech and document-analysis probes use their separate bounded scripts."
	$(PYTHON) -m pytest backend/app/tests -q -m live

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
