.PHONY: install test lint run agent build-maya capture capture-selftest package doctor sis-onboard e2e smoke clean
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

doctor:                     ## check every precondition for the real SIS run
	python3 scripts/e2e/doctor.py

sis-onboard: doctor        ## doctor → capture real selectors → verify → start the stack
	python3 scripts/capture/capture_selectors.py --serial $(SERIAL) --missing-serial $(MISSING)
	python3 scripts/e2e/doctor.py
	$(MAKE) e2e

capture:                    ## headed, human-in-the-loop SIS selector capture
	python3 scripts/capture/capture_selectors.py --serial $(SERIAL) --missing-serial $(MISSING)

capture-selftest:           ## prove the capture engine against the local fixture
	python3 scripts/capture/capture_selectors.py --self-test

package:                    ## build the hand-off archive (chat + automation + latest self-test proof)
	@rm -rf /tmp/maya-package && mkdir -p /tmp/maya-package
	@find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@rm -rf services/automation/.pytest_cache
	@cp -r ../maya /tmp/maya-package/maya && rm -rf /tmp/maya-package/maya/artifacts
	@mkdir -p /tmp/maya-package/maya/artifacts/capture
	@latest=$$(ls -dt artifacts/capture/self-test-* 2>/dev/null | head -1); \
	  [ -n "$$latest" ] && cp -r "$$latest" /tmp/maya-package/maya/artifacts/capture/ || true
	@cd /tmp/maya-package && zip -qr "$(CURDIR)/../maya-platform.zip" maya -x '*.pyc'
	@echo "wrote ../maya-platform.zip"

e2e:                        ## start the whole stack: gateway + Maya chat + live worker
	python3 scripts/e2e/run_e2e.py $(E2E_ARGS)

smoke:
	curl -s localhost:8080/readyz | python3 -m json.tool
	curl -s -X POST localhost:8080/v1/equipment/search \
	  -H 'content-type: application/json' -H 'x-maya-actor: dev' \
	  -d '{"serial_number":"SN123456"}' | python3 -m json.tool

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} + ; rm -rf .pytest_cache
