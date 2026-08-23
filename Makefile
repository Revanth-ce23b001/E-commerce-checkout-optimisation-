# Commands per CLAUDE.md. Targets that need decisions still under review print
# what is blocking rather than running a half-specified pipeline.

PY := .venv/Scripts/python.exe
ifeq ($(OS),)
PY := .venv/bin/python
endif

.PHONY: help setup test dev generate validate load all clean

help:
	@echo "make setup     - create .venv and install the pinned stack"
	@echo "make test      - pytest: unit tests for GENERATOR CODE"
	@echo "make dev       - generate the 5,000-order development dataset  [BLOCKED]"
	@echo "make generate  - generate the full 100K+ dataset               [BLOCKED]"
	@echo "make validate  - run the validation suite                      [BLOCKED]"
	@echo "make load      - load PostgreSQL, create views, apply REVOKE   [BLOCKED]"
	@echo "make all       - generate -> validate -> load                  [BLOCKED]"

setup:
	python -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"

test:
	$(PY) -m pytest

# --- gated targets ---------------------------------------------------------
# Stage 3 onwards is blocked pending decisions A2, A4, A6 and A7-A24.
# See docs/decision_register.md.

BLOCKED = @echo "BLOCKED: config/params.yaml is not complete." && \
	echo "Open decisions: A2 (conversion model), A4 (RTO reason driver weights)," && \
	echo "A6 (GT bands), A7-A24. See docs/decision_register.md." && exit 1

dev:
	$(BLOCKED)

generate:
	$(BLOCKED)

validate:
	$(BLOCKED)

load:
	$(BLOCKED)

all: generate validate load

clean:
	rm -rf data/raw/* data/processed/* data/validation/* reports/figures/*
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache
