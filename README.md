# E-commerce Checkout Optimization — Reducing RTO While Protecting Conversion and Contribution Margin

A reproducible simulation of an Indian e-commerce marketplace (~100,000 orders, 90-day
window), built to support a Product Management case study on COD behaviour, RTO risk,
checkout conversion, and contribution margin.

**The objective is not "generate 100K fake rows."** It is to plant a known ground truth —
including truths that are *hard to recover* — so later analysis can be checked against it.
A dataset where the obvious analysis produces the obvious right answer is a failed dataset.

## Status

**Phase 2B complete and database-verified** — tag `phase2b-verified`.

105,605 orders across 155,000 checkout sessions, loaded into PostgreSQL and
checked against the live schema.

| | |
|---|---|
| Validation | **65 tests · 59 pass · 0 HARD fail · 0 SOFT fail · 6 skip** |
| The 6 skips | All Phase-5-deferred (need fitted models). **Zero environment-blocked** |
| Load | 14 tables, 2,022,081 rows, every parquet count matching the server exactly |
| Leakage firewall | LK-05 verified by connecting **as the `analyst` role** and having both `truth` reads refused with SQLSTATE 42501 |
| Referential integrity | 21 foreign keys, 0 orphans · 102 CHECK predicates, 0 violations |
| Reproducibility | DQ-01: regenerated from the same seed, `fct_order` content hash identical |

Verdict is 🟡 CONDITIONAL rather than 🟢 solely because six HARD tests need a
fitted model, which is Phase 5's job. Every one is listed with its reason in
`reports/data_validation_report.md` §5 and `docs/limitations.md`.

Applying the DDL to real rows for the first time found **six defects** that
neither the 42 data-validation tests nor the 146 unit tests had caught — see
decision **A44**. The load now runs a pre-flight that blocks on any declared
column that is absent from the frame or entirely NULL.

One open item needs a ruling: `logit_cod_components` and `logit_rto_components`
are declared JSONB and are entirely NULL (limitation **L12**). It is a registered
gap, printed on every load, not a hidden one.

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

## PostgreSQL

Needed for the load (module 22) and for the three tests that cannot run without a
server: **LK-01** (view column list), **LK-05** (role grants) and **DQ-01**
(reproducibility).

```bash
docker run -d --name rto-postgres \
  -e POSTGRES_PASSWORD=<password> -e POSTGRES_DB=checkout_rto \
  -p 5434:5432 postgres:16-alpine
```

Copy `config/database.env.example` to `config/database.env` and fill in the
password. That file is gitignored; the example is not.

```bash
python scripts/02_load_postgres.py --dry-run   # parse the DDL, no server needed
python scripts/02_load_postgres.py --force     # drop, recreate, COPY all 14 tables
python scripts/04_verify_database.py           # LK-01, LK-05, DQ-01, constraints
```

### Windows: Hyper-V reserves TCP ports silently

With Hyper-V or WSL2 enabled, Windows reserves large blocks of TCP ports. Binding
one fails with a message that reads like a permissions problem rather than a
reservation:

```
bind: An attempt was made to access a socket in a way forbidden by its
access permissions.
```

During this build **55432, 5433 and 5432 were all unavailable** and 5434 worked —
which is why the default `PGPORT` is 5434 rather than the conventional 5432. The
reserved ranges change across reboots. To list them:

```powershell
netsh interface ipv4 show excludedportrange protocol=tcp
```

Pick a port outside every excluded range. This is not a Docker fault and retrying
will not help.

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
