# E-commerce Checkout Optimization — Reducing RTO While Protecting Conversion and Contribution Margin

A reproducible simulation of an Indian e-commerce marketplace (~100,000 orders, 90-day
window), built to support a Product Management case study on COD behaviour, RTO risk,
checkout conversion, and contribution margin.

**The objective is not "generate 100K fake rows."** It is to plant a known ground truth —
including truths that are *hard to recover* — so later analysis can be checked against it.
A dataset where the obvious analysis produces the obvious right answer is a failed dataset.

## Status

**Phase 2B — generation modules 02–12 built and checkpointed.**

Dimensions, latents, pre-window history, sessions, point-in-time state and the full checkout
funnel exist. Four intercepts solve cleanly: COD share lands on 0.6200 and conversion on
0.6762. Modules 13–23 (orders, cancellations, RTO, economics) are not started.

Open, both needing a ruling:

- **A31** — VOL-01 (≥100,000 orders), VOL-02 (145–150k sessions) and CAL-06 (68% ±2pp) are
  jointly knife-edge. The realised conversion sits comfortably inside CAL-06 and still
  produces **99,441 orders**, so VOL-01 fails.
- **A28** — distribution values for modules 08–12, tagged `[A28 PROPOSED]`.

See `docs/decision_register.md`.

## Source of truth

| File | Role |
|---|---|
| `docs/00_phase1_blueprint.md` | Business framing, unit economics, metrics, opportunity model |
| `docs/01_phase2_data_architecture.md` | **The implementation spec.** Primary reference |
| `docs/02_implementation_brief.md` | Build instructions, stage gates, validation suite |
| `CLAUDE.md` | Project invariants — never change without asking |

## Directory guide

| Path | Contains |
|---|---|
| `config/` | `params.yaml` — the single source of business assumptions — plus its JSON schema and sensitivity scenarios |
| `src/config/` | Config loading, schema validation, SHA-256 hashing, seed substream harness |
| `src/generators/` | One module per generation step, in forced dependency order |
| `src/models/` | Shared logit assembly with component tracing; calibration bisection |
| `src/economics/` | Every cost line, in one place |
| `src/validation/` | Tests the **generated data** against business targets (7 families) |
| `tests/` | Tests the **generator code** (pytest). Deliberately separate from `src/validation/` |
| `sql/` | Schema DDL, indexes, views, and the leakage-firewall view |
| `data/truth/` | `_truth.json` — planted effect sizes and run manifest. Committed |
| `data/manifests/` | Run hashes for the DQ-01 reproducibility test. Committed |
| `reports/` | Generated validation report |

## Commands

```bash
make setup       # create .venv and install pinned dependencies
make dev         # generate the 5,000-order development dataset
make generate    # generate the full 100K+ dataset
make validate    # run the validation suite -> reports/data_validation_report.md
make load        # load PostgreSQL, create views, apply REVOKE
make test        # pytest — unit tests for generator code
make all         # generate -> validate -> load
```

### Without `make`

`make` is not required. Every target is a thin wrapper around a script, and the underlying
invocation is always available:

| Make target | Direct equivalent |
|---|---|
| `make test` | `.venv/Scripts/python.exe -m pytest` |
| `make dev` | `.venv/Scripts/python.exe scripts/01_generate.py --dev` |
| `make generate` | `.venv/Scripts/python.exe scripts/01_generate.py` |
| `make load` | `.venv/Scripts/python.exe scripts/02_load_postgres.py --force` |
| — | `.venv/Scripts/python.exe scripts/02_load_postgres.py --dry-run` |

On macOS/Linux the interpreter is `.venv/bin/python`.

```bash
# Useful flags
python scripts/01_generate.py --no-write      # run the checkpoint, write nothing
python scripts/01_generate.py --seed 7        # override seed.master for a robustness check
python scripts/02_load_postgres.py --dry-run  # parse the DDL, no server needed
```

`--dry-run` parses every statement in `sql/*.sql` against the postgres dialect and reports
failures with the offending statement. It catches structural errors at zero cost. It does
**not** catch semantic ones — a missing referenced table, a duplicate constraint name, a
cross-table type mismatch — and it cannot verify the REVOKE grants, which is LK-05 and needs
a live server. A clean dry-run means "this will parse", not "this will apply".

## Key invariants

- Only intercepts may be calibrated. **Every slope coefficient is immutable** (test CAL-09).
- Latents (`latent_trust`, `latent_liquidity`, `latent_intent`, `latent_price_sensitivity`)
  live only in PostgreSQL schema `truth`. The `analyst` role has REVOKE ALL on it.
- Risk-model AUC on safe features must be **< 0.85**. If it isn't, something leaked (LK-03).
- `is_shipped` is the RTO-rate denominator. Pre-ship cancellations are removed before the RTO draw.
- Randomness uses `SeedSequence` substreams, never a global seed. Same seed + same params
  hash produces byte-identical output (DQ-01).
- Never edit generated rows to pass validation. Fix a parameter, regenerate, re-validate.

See `CLAUDE.md` for the full list.
