.PHONY: install test lint run agent build-maya capture capture-selftest smoke clean
SERIAL ?= CAT0336LKBW00123
MISSING ?= ZZZ00000

install:
	pip install -r services/automation/requirements-dev.txt
	python -m playwright install chromium

test:
	cd services/automation && python -m pytest -q
	cd frontend && node test_equipment.mjs
	python3 scripts/capture/capture_selectors.py --self-test

lint:
	cd services/automation && ruff check app tests
	cd frontend && node --check maya-equipment.js

run:
	cd services/automation && uvicorn app.main:app --reload --port 8080

agent:
	python -m agent.maya_agent

# Rebuild Maia v9 from v8 + the equipment layer. Fails loudly if an anchor moved.
build-maya:
	cd frontend && python3 build_v9.py

capture:                    ## headed, human-in-the-loop SIS selector capture
	python3 scripts/capture/capture_selectors.py --serial $(SERIAL) --missing-serial $(MISSING)

capture-selftest:           ## prove the capture engine against the local fixture
	python3 scripts/capture/capture_selectors.py --self-test

smoke:
	curl -s localhost:8080/readyz | python3 -m json.tool
	curl -s -X POST localhost:8080/v1/equipment/search \
	  -H 'content-type: application/json' -H 'x-maya-actor: dev' \
	  -d '{"serial_number":"SN123456"}' | python3 -m json.tool

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} + ; rm -rf .pytest_cache
