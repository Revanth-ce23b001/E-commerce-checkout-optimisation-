# Commands per CLAUDE.md. Targets that need decisions still under review print
# what is blocking rather than running a half-specified pipeline.

PY := .venv/Scripts/python.exe
ifeq ($(OS),)
PY := .venv/bin/python
endif

.PHONY: help setup test dev generate dryrun validate load verify baseline m1 m2 gt03 interventions all clean

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
	@echo "make m1        - Phase 4: rules baseline + M1  -> reports/phase4_m1.md"
	@echo "make m2        - Phase 4: M2 + challenger + A47 -> reports/phase4_m2.md"
	@echo "make gt03      - GT-03 diagnostics (A50)      -> reports/gt03_diagnostics.md"
	@echo "make interventions - Phase 5: lever simulation + decision table -> reports/phase5_interventions.md"
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

# Phase 4. Needs `load` to have run: it reads analytics.vw_risk_model_input
# through the restricted `analyst` role, which is the only permitted source.
m1:
	$(PY) scripts/06_fit_m1.py

# Phase 4 Stage 2. Also publishes reports/fairness_checks.json, which FA-01
# reads -- so `make validate` after `make m2` is what turns FA-01 from SKIP
# into a real result.
m2:
	$(PY) scripts/07_fit_m2.py

# GT-03 diagnostics (A50). Measures WHY the adjustment closes 70.9% of a 65%
# ceiling; changes nothing. Reads data/processed/h1_population.parquet, so
# the Phase 3 analysis must have run. Writes reports/gt03_diagnostics.md.
gt03:
	$(PY) scripts/08_gt03_diagnostics.py

# Phase 5 Stage 1. Needs `load` (the view is the only permitted feature
# source) and `m2` (m2_scores.parquet is the scored population, consumed
# and never re-fitted). Reads config/interventions.yaml for every [A].
interventions:
	$(PY) scripts/09_interventions.py

all: generate load verify validate

clean:
	rm -rf data/raw/* data/processed/* data/validation/* reports/figures/*
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache
