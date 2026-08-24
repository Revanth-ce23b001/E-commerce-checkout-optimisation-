"""Run the checks that need a live PostgreSQL: LK-01, LK-05, DQ-01, and the
constraints the parquet layer never enforced.

    python scripts/04_verify_database.py            # all checks
    python scripts/04_verify_database.py --manifest # write the DQ-01 baseline

Why this is separate from ``03_validate.py``: those tests read parquet and need
no server. These need a live database, a real ``analyst`` login, and a prior
manifest. Keeping them apart is what lets 03 report an honest SKIP instead of
silently passing them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd  # noqa: E402

from scripts.load_helpers import connect, read_env  # noqa: E402
from src.validation.dataset_hash import order_hash as _canonical_hash  # noqa: E402

MANIFEST_DIR = REPO_ROOT / "data" / "manifests"
TRUTH_FILE = REPO_ROOT / "data" / "truth" / "_truth.json"
ORDER_PARQUET = REPO_ROOT / "data" / "raw" / "fct_order.parquet"

ANALYST_PASSWORD = "analyst_dev_only"
RESULTS_FILE = REPO_ROOT / "reports" / "database_checks.json"


def order_hash(path: Path = ORDER_PARQUET) -> str:
    """SHA-256 of fct_order's CONTENT. Definition lives in one place only."""
    return _canonical_hash(pd.read_parquet(path))


def write_manifest() -> Path:
    truth = json.loads(TRUTH_FILE.read_text(encoding="utf-8"))
    seed = truth["run_manifest"]["master_seed"]
    manifest = {
        "master_seed": seed,
        "params_sha256": truth["run_manifest"]["params_sha256"],
        "dgp_sha256": truth["run_manifest"]["dgp_sha256"],
        "generator_version": truth["run_manifest"]["generator_version"],
        "fct_order_sha256": order_hash(),
        "n_orders": int(pd.read_parquet(ORDER_PARQUET).shape[0]),
        "note": ("DQ-01 baseline. A manifest compared against the run that wrote "
                 "it proves nothing; regenerate from the same seed and compare."),
    }
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    path = MANIFEST_DIR / f"run_{seed}.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def check_dq_01() -> tuple[bool, str]:
    truth = json.loads(TRUTH_FILE.read_text(encoding="utf-8"))
    seed = truth["run_manifest"]["master_seed"]
    path = MANIFEST_DIR / f"run_{seed}.json"
    if not path.exists():
        return False, f"No manifest at {path}. Run with --manifest first."

    manifest = json.loads(path.read_text(encoding="utf-8"))
    current = order_hash()
    params_now = truth["run_manifest"]["params_sha256"]

    if manifest["params_sha256"] != params_now:
        return False, (
            f"params.yaml changed since the baseline ({manifest['params_sha256'][:12]}"
            f" -> {params_now[:12]}). A mismatch here means the CONFIG moved, not "
            "the generator."
        )
    if manifest["fct_order_sha256"] != current:
        return False, (f"fct_order hash differs. baseline "
                       f"{manifest['fct_order_sha256'][:32]}... vs current "
                       f"{current[:32]}...")
    return True, (f"identical across regeneration: {current[:32]}... "
                  f"({manifest['n_orders']:,} orders, seed {seed})")


def check_lk_01(connection, params) -> tuple[bool, str]:
    """The view's column list must be a subset of the safe whitelist."""
    whitelist = set(params.require("leakage_guard.safe_feature_whitelist"))
    blocked = set(params.require("leakage_guard.hard_blocked"))
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='analytics' AND table_name='vw_risk_model_input' "
            "ORDER BY ordinal_position;")
        columns = [r[0] for r in cursor.fetchall()]
    if not columns:
        return False, "vw_risk_model_input does not exist."

    leaked = sorted(set(columns) & blocked)
    outside = sorted(set(columns) - whitelist)
    if leaked:
        return False, f"HARD-BLOCKED column(s) present: {leaked}"
    if outside:
        return False, f"{len(outside)} column(s) not on the whitelist: {outside}"
    return True, f"all {len(columns)} view columns are on the safe whitelist"


def check_lk_05(env) -> tuple[bool, str]:
    """Open a real connection AS analyst and attempt to read truth.

    pg_catalog inspection shows what the DDL INTENDED. Only a denied SELECT shows
    what is ENFORCED — which is the entire reason this test exists.
    """
    import psycopg2
    from psycopg2 import errorcodes

    admin = connect(env)
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute("ALTER ROLE analyst WITH LOGIN PASSWORD %s;", (ANALYST_PASSWORD,))
        # A PUBLIC grant bypasses a per-role REVOKE entirely.
        cursor.execute("SELECT count(*) FROM information_schema.role_table_grants "
                       "WHERE grantee='PUBLIC' AND table_schema='truth';")
        public_grants = int(cursor.fetchone()[0])
        # Inherited privileges override a role's own grants.
        cursor.execute("SELECT count(*) FROM pg_auth_members m "
                       "JOIN pg_roles member ON m.member = member.oid "
                       "WHERE member.rolname = 'analyst';")
        memberships = int(cursor.fetchone()[0])
        # A truth table created outside schema truth would sidestep the REVOKE.
        cursor.execute("SELECT count(*) FROM information_schema.tables "
                       "WHERE table_name LIKE 'truth\\_%' AND table_schema <> 'truth';")
        stray = int(cursor.fetchone()[0])
    admin.close()

    problems = []
    if public_grants:
        problems.append(f"{public_grants} PUBLIC grant(s) on schema truth")
    if memberships:
        problems.append(f"analyst inherits from {memberships} role(s)")
    if stray:
        problems.append(f"{stray} truth_* table(s) outside schema truth")

    denied, allowed = [], []
    session = connect(env, user="analyst", password=ANALYST_PASSWORD)
    session.autocommit = True
    for table in ("truth.truth_customer_latent", "truth.truth_order_probability"):
        try:
            with session.cursor() as cursor:
                cursor.execute(f"SELECT count(*) FROM {table};")
            allowed.append(table)
        except psycopg2.Error as exc:
            if exc.pgcode == errorcodes.INSUFFICIENT_PRIVILEGE:
                denied.append(table)
            else:
                problems.append(f"{table}: unexpected SQLSTATE {exc.pgcode}")

    # Belt and braces: a role that can read NOTHING is a broken role, not a
    # working boundary.
    readable = -1
    try:
        with session.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM analytics.fct_order;")
            readable = int(cursor.fetchone()[0])
    except psycopg2.Error as exc:
        problems.append(f"analyst CANNOT read analytics.fct_order ({exc.pgcode}) — "
                        "the role is broken, not secure")
    session.close()

    if allowed:
        problems.append(f"analyst COULD read {allowed}")
    if len(denied) != 2:
        problems.append(f"only {len(denied)}/2 truth tables denied")
    if problems:
        return False, "; ".join(problems)
    return True, (
        "connected AS analyst: both truth tables denied with SQLSTATE 42501; "
        f"analytics.fct_order readable ({readable:,} rows); 0 PUBLIC grants, "
        "0 role memberships, 0 stray truth tables")


def check_constraints(env) -> list[tuple[str, bool, str]]:
    """Re-verify every FK and CHECK by SCANNING the loaded rows.

    ``ALTER TABLE ... VALIDATE CONSTRAINT`` was the obvious implementation and it
    is worthless here: a constraint created normally is already marked valid, so
    VALIDATE returns success without reading a single row. That is the same
    intent-vs-enforcement trap as inspecting pg_catalog for LK-05.

    So each constraint is turned back into a query that looks for rows violating
    it: an anti-join per foreign key, and ``WHERE (predicate) IS FALSE`` per check
    (IS FALSE, not NOT: a CHECK is satisfied when its predicate is NULL).
    """
    connection = connect(env)
    connection.autocommit = True

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT c.conname,
                   c.conrelid::regclass::text,
                   c.confrelid::regclass::text,
                   (SELECT array_agg(a.attname ORDER BY x.ord)
                      FROM unnest(c.conkey) WITH ORDINALITY AS x(attnum, ord)
                      JOIN pg_attribute a
                        ON a.attrelid = c.conrelid AND a.attnum = x.attnum),
                   (SELECT array_agg(a.attname ORDER BY x.ord)
                      FROM unnest(c.confkey) WITH ORDINALITY AS x(attnum, ord)
                      JOIN pg_attribute a
                        ON a.attrelid = c.confrelid AND a.attnum = x.attnum)
              FROM pg_constraint c
             WHERE c.contype = 'f'
               AND c.connamespace IN ('analytics'::regnamespace, 'truth'::regnamespace)
             ORDER BY c.conname;""")
        fks = cursor.fetchall()

        cursor.execute("""
            SELECT conname, conrelid::regclass::text, pg_get_constraintdef(oid)
              FROM pg_constraint
             WHERE contype = 'c'
               AND connamespace IN ('analytics'::regnamespace, 'truth'::regnamespace)
             ORDER BY conname;""")
        checks = cursor.fetchall()

    fk_bad, fk_rows = [], 0
    for name, child, parent, child_cols, parent_cols in fks:
        on = " AND ".join(f"p.{pc} = c.{cc}"
                          for cc, pc in zip(child_cols, parent_cols))
        not_null = " AND ".join(f"c.{cc} IS NOT NULL" for cc in child_cols)
        sql = (f"SELECT count(*) FROM {child} c WHERE {not_null} "
               f"AND NOT EXISTS (SELECT 1 FROM {parent} p WHERE {on});")
        with connection.cursor() as cursor:
            cursor.execute(sql)
            orphans = int(cursor.fetchone()[0])
        fk_rows += orphans
        if orphans:
            fk_bad.append(f"{child}.{name}: {orphans:,} orphan(s)")

    check_bad = []
    for name, table, definition in checks:
        predicate = definition[len("CHECK "):].strip()
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT count(*) FROM {table} WHERE ({predicate}) IS FALSE;")
            bad = int(cursor.fetchone()[0])
        if bad:
            check_bad.append(f"{table}.{name}: {bad:,} row(s)")
    connection.close()

    results = [
        ("FK constraints hold (no orphans)", not fk_bad,
         "; ".join(fk_bad) if fk_bad else
         f"{len(fks)} foreign keys anti-joined against the loaded rows, 0 orphans"),
        ("CHECK constraints hold", not check_bad,
         "; ".join(check_bad) if check_bad else
         f"{len(checks)} check predicates re-evaluated row by row, 0 violations"),
    ]
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", action="store_true",
                        help="write the DQ-01 baseline manifest and exit")
    args = parser.parse_args(argv)

    if args.manifest:
        path = write_manifest()
        print(f"wrote {path}")
        print("Baseline established. Regenerate from the same seed, then re-run "
              "without --manifest so DQ-01 compares two independent runs.")
        return 0

    from src.config.loader import load_params

    params = load_params(REPO_ROOT / "config" / "params.yaml",
                         REPO_ROOT / "config" / "params.schema.json")
    env = read_env()
    connection = connect(env)

    rule = "=" * 78
    print(f"{rule}\nDATABASE-BACKED VERIFICATION\n{rule}")

    outcomes = []
    outcomes.append(("LK-01", "View columns subset of safe whitelist",
                     *check_lk_01(connection, params)))
    outcomes.append(("LK-05", "analyst is DENIED on schema truth", *check_lk_05(env)))
    outcomes.append(("DQ-01", "Reproducibility: fct_order hash", *check_dq_01()))
    connection.close()
    for name, passed, detail in check_constraints(env):
        outcomes.append(("CONSTR", name, passed, detail))

    for test_id, name, passed, detail in outcomes:
        print(f"\n[{'PASS' if passed else 'FAIL'}] {test_id}  {name}")
        print(f"       {detail}")

    # Publish, so scripts/03_validate.py can report these three as real results
    # instead of SKIPs. The dataset hash goes in with them: without it a stale
    # file would silently turn a SKIP into a fabricated PASS, which is the same
    # failure mode as a manifest compared against itself.
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_FILE.write_text(json.dumps({
        "fct_order_sha256": order_hash(),
        "checks": {test_id: {"passed": bool(passed), "detail": detail}
                   for test_id, _, passed, detail in outcomes},
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {RESULTS_FILE.relative_to(REPO_ROOT)}")

    failures = [o for o in outcomes if not o[2]]
    print(f"\n{rule}")
    print(f"{len(outcomes)} checks | {len(outcomes) - len(failures)} pass "
          f"| {len(failures)} FAIL")
    print(rule)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
