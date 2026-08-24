# Commands per CLAUDE.md. Targets that need decisions still under review print
# what is blocking rather than running a half-specified pipeline.

PY := .venv/Scripts/python.exe
ifeq ($(OS),)
PY := .venv/bin/python
endif

.PHONY: help setup test dev generate dryrun validate load all clean

# Every target below is a thin wrapper. README documents the direct `python
# scripts/...` invocation for each, so nothing is blocked on having make installed.

help:
	@echo "make setup     - create .venv and install the pinned stack"
	@echo "make test      - pytest: unit tests for GENERATOR CODE"
	@echo "make dev       - modules 02-07 at 5,000-order dev scale"
	@echo "make generate  - modules 02-07 at full scale  [08+ NOT BUILT]"
	@echo "make dryrun    - parse sql/*.sql with sqlglot; no server needed"
	@echo "make validate  - run the validation suite     [BLOCKED]"
	@echo "make load      - load PostgreSQL + REVOKE     [BLOCKED]"
	@echo "make all       - generate -> validate -> load [BLOCKED]"

setup:
	python -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"

test:
	$(PY) -m pytest

# --- built -----------------------------------------------------------------
# Modules 02-07 only: dates, geography, sellers, products, customers + latents,
# pre-window history. Ends with the Stage-2 checkpoint.

dev:
	$(PY) scripts/01_generate.py --dev

generate:
	$(PY) scripts/01_generate.py

dryrun:
	$(PY) scripts/02_load_postgres.py --dry-run

# --- gated targets ---------------------------------------------------------
# Modules 08-23 are not written. validate and load read tables that do not exist
# yet, so they refuse rather than failing halfway through.

BLOCKED = @echo "BLOCKED: generation modules 08-23 are not built yet." && \
	echo "Built: 02 dates, 03 geography, 04 sellers, 05 products," && \
	echo "       06 customers + latents, 07 pre-window history." && \
	echo "Next:  08 sessions onward. A26 fixes the 11a/11b/11c ordering." && \
	echo "See docs/decision_register.md." && exit 1

validate:
	$(BLOCKED)

load:
	$(BLOCKED)

all: generate validate load

clean:
	rm -rf data/raw/* data/processed/* data/validation/* reports/figures/*
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache
