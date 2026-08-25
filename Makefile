# Commands per CLAUDE.md. Targets that need decisions still under review print
# what is blocking rather than running a half-specified pipeline.

PY := .venv/Scripts/python.exe
ifeq ($(OS),)
PY := .venv/bin/python
endif

.PHONY: help setup test dev generate dryrun validate load verify baseline all clean

# Every target below is a thin wrapper. README documents the direct `python
# scripts/...` invocation for each, so nothing is blocked on having make installed.

help:
	@echo "make setup     - create .venv and install the pinned stack"
	@echo "make test      - pytest: unit tests for GENERATOR CODE"
	@echo "make dev       - modules 02-07 at 5,000-order dev scale"
	@echo "make generate  - modules 02-07 at full scale  [08+ NOT BUILT]"
	@echo "make dryrun    - parse sql/*.sql with sqlglot; no server needed"
	@echo "make load      - load PostgreSQL + REVOKE (DROPS both schemas)"
	@echo "make verify    - LK-01, LK-05, DQ-01, FK and CHECK against the server"
	@echo "make validate  - run the validation suite     -> reports/"
	@echo "make all       - generate -> load -> verify -> validate"

setup:
	python -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"

test:
	$(PY) -m pytest

# --- built -----------------------------------------------------------------
# Modules 02-21: dimensions, latents, pre-window history, sessions, the
# decision-A1 day loop, RTO reasons, economics, roll-up and the truth file.

dev:
	$(PY) scripts/01_generate.py --dev

generate:
	$(PY) scripts/01_generate.py

dryrun:
	$(PY) scripts/02_load_postgres.py --dry-run

# --- database-backed --------------------------------------------------------
# `load` DROPS and recreates both schemas, so it refuses without --force.
# `verify` must run AFTER `load`: it publishes reports/database_checks.json,
# which `validate` reads to report LK-01, LK-05 and DQ-01 as real results
# instead of SKIPs. That file is gated on BOTH the dataset hash and the DDL
# hash, so running these out of order degrades to SKIP rather than lying.

load:
	$(PY) scripts/02_load_postgres.py --force

verify:
	$(PY) scripts/04_verify_database.py

# Writes the DQ-01 baseline. Run it, regenerate, then `make verify` -- a
# manifest compared against the run that wrote it proves nothing.
baseline:
	$(PY) scripts/04_verify_database.py --manifest

validate:
	$(PY) scripts/03_validate.py

all: generate load verify validate

clean:
	rm -rf data/raw/* data/processed/* data/validation/* reports/figures/*
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache
