"""Module 22 — load the generated dataset into PostgreSQL.

Two modes:

    python scripts/02_load_postgres.py --dry-run   # parse sql/*.sql, no server
    python scripts/02_load_postgres.py --force     # drop, recreate, COPY, verify

``--force`` is required for a real load because the load is destructive: it
drops both schemas and recreates them. Idempotent by construction — running it
twice gives the same result, and there is no partial state to reason about.

Rows are moved with ``COPY`` through an in-memory CSV buffer, never row-by-row
inserts. Every table's loaded count is compared against its parquet count and
**a mismatch is an error, not a warning**: a short table would still satisfy
every foreign key and would fail silently for the rest of the project's life.

Connection details come from ``config/database.env`` (gitignored). See
``config/database.env.example``.
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.load_helpers import connect, read_env  # noqa: E402

# SCHEMA files only. The analysis library lives in sql/analysis/ and must NOT be
# swept in here: those files are SELECT queries, and applying them as DDL runs
# them against an empty schema at load time. Found the honest way -- adding the
# Phase 3 query library to sql/ made this loader try to execute it, and a
# NULLIF-guarded ratio raised DivisionByZero on a table with no rows.
#
# The glob is narrowed rather than the files renamed, so a future analysis file
# cannot re-enter the DDL path by being named carelessly.
SQL_DIR = REPO_ROOT / "sql"
ANALYSIS_DIR = SQL_DIR / "analysis"
DATA_DIR = REPO_ROOT / "data" / "raw"
ENV_FILE = REPO_ROOT / "config" / "database.env"

# Load order is forced by the foreign keys: a table cannot be loaded before
# anything it references. fct_checkout_session.order_id is a DEFERRABLE FK, which
# is what lets sessions load before the orders they point at.
LOAD_ORDER = [
    "dim_date", "dim_geography", "dim_seller", "dim_product", "dim_customer",
    "fct_checkout_session", "fct_customer_state_at_session",
    "fct_checkout_event", "fct_payment_attempt",
    "fct_order", "fct_delivery_event", "fct_order_economics",
    "truth_customer_latent", "truth_order_probability",
]
TRUTH_TABLES = {"truth_customer_latent", "truth_order_probability"}

# Decision A44. A declared column that is entirely NULL is a generator defect and
# the pre-flight blocks the load over it. The parquet layer enforces nothing, so
# this is the first place a never-populated column can be caught.
#
# There are now no registered all-NULL columns. The two that used to be here --
# logit_cod_components and logit_rto_components -- were populated under decision
# A45 for a documented 2,000-session audit sample. The dictionary stays, because
# the mechanism is the point: an exception that is invisible is indistinguishable
# from a bug, and the next gap must be registered here to load at all.
KNOWN_EMPTY: dict[tuple[str, str], str] = {}

# Decision A45. Columns that are populated for a DOCUMENTED SUBSET rather than
# for every row. Each names the BOOLEAN column that states, per row, whether the
# value should be there.
#
# This is a stricter check than KNOWN_EMPTY, not a softer one. A registered
# all-NULL column only has to be null; these have to be null in exactly the rows
# the flag says, and non-null in exactly the rows it says. So the sample silently
# collapsing to zero rows -- the realistic failure, and one no all-NULL check
# would catch once a single row was populated -- fails the load.
PARTIAL_BY_DESIGN = {
    ("truth_order_probability", "logit_cod_components"): (
        "components_populated", "A45 audit sample; populated wherever the flag is true"),
    ("truth_order_probability", "logit_rto_components"): (
        "components_populated", "A45 audit sample; populated where the flag is true "
        "AND the session produced an order"),
}


def check_partial(name: str, frame) -> tuple[list[str], list[str]]:
    """Verify each partially-populated column against the flag that explains it.

    Returns (problems, notes). A problem blocks the load.
    """
    problems, notes = [], []
    for (table, column), (flag, reason) in PARTIAL_BY_DESIGN.items():
        if table != name or column not in frame.columns:
            continue
        if flag not in frame.columns:
            problems.append(
                f"{column} is declared partial but its flag {flag} is absent")
            continue
        present = frame[column].notna()
        flagged = frame[flag].fillna(False).astype(bool)
        if not present.any():
            problems.append(
                f"{column} is 100% NULL -- the {flag} sample produced nothing")
            continue
        # The RTO trace is a subset of the flag (no order, no RTO logit), so the
        # rule is containment; the COD trace must match the flag exactly.
        if (present & ~flagged).any():
            problems.append(
                f"{column} is populated on {(present & ~flagged).sum():,} row(s) "
                f"where {flag} is false")
        if column.startswith("logit_cod") and (flagged & ~present).any():
            problems.append(
                f"{column} is NULL on {(flagged & ~present).sum():,} row(s) "
                f"where {flag} is true")
        notes.append(
            f"{column}: {int(present.sum()):,} of {len(frame):,} rows populated "
            f"({present.mean():.3%}) -- {reason}")
    return problems, notes


# ---------------------------------------------------------------------------
# --dry-run
# ---------------------------------------------------------------------------


def split_statements(sql_text: str) -> list[str]:
    """Split on semicolons, skipping line comments, dollar-quoting and strings.

    All three bit on the first attempt. This file's comments are prose, and prose
    contains semicolons ("Primary keys everywhere; foreign keys enforced..."),
    which tore CREATE TABLE statements in half and produced a page of parse
    errors that were artefacts of the splitter rather than the DDL.
    """
    statements, current = [], []
    in_dollar = in_quote = False
    i, n = 0, len(sql_text)
    while i < n:
        if not in_quote and sql_text.startswith("$$", i):
            in_dollar = not in_dollar
            current.append("$$")
            i += 2
            continue
        char = sql_text[i]
        if not in_quote and not in_dollar and sql_text.startswith("--", i):
            newline = sql_text.find("\n", i)
            i = n if newline == -1 else newline
            continue
        if char == "'" and not in_dollar:
            if in_quote and sql_text.startswith("''", i):
                current.append("''")
                i += 2
                continue
            in_quote = not in_quote
        if char == ";" and not in_dollar and not in_quote:
            statements.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        i += 1
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return [s for s in statements if s]


def dry_run(sql_files: list[Path]) -> int:
    """Parse every statement with sqlglot. Structural check only."""
    try:
        import sqlglot
        from sqlglot import expressions as exp
        from sqlglot.errors import ParseError
    except ImportError:
        print("ERROR: sqlglot not installed.")
        return 2

    logging.getLogger("sqlglot").setLevel(logging.ERROR)
    total, failures, opaque = 0, [], []
    for path in sql_files:
        statements = split_statements(path.read_text(encoding="utf-8"))
        bad = 0
        for statement in statements:
            total += 1
            try:
                parsed = sqlglot.parse_one(statement, dialect="postgres")
                if parsed is None:
                    raise ParseError("parsed to nothing")
                if isinstance(parsed, exp.Command):
                    opaque.append((path.name, statement.splitlines()[0][:70]))
            except ParseError as exc:
                bad += 1
                failures.append((path.name, statement.splitlines()[0][:70], str(exc)))
        print(f"{'OK  ' if bad == 0 else 'FAIL'}  {path.name:<32} {len(statements):>3} statement(s)")

    print()
    if failures:
        for name, line, message in failures:
            print(f"  {name}: {line}\n    {message}\n")
        return 1
    print(f"All {total} statement(s) parsed cleanly.")
    if opaque:
        print(f"{len(opaque)} parsed as opaque Commands and were NOT syntax-checked "
              "(GRANT/REVOKE/DO). LK-05 verifies those for real.")
    print("NOTE: parsing is not validation. Semantic errors need a live server.")
    return 0


# ---------------------------------------------------------------------------
# --force: the real load
# ---------------------------------------------------------------------------


def apply_ddl(connection, sql_files: list[Path]) -> None:
    with connection.cursor() as cursor:
        cursor.execute("DROP SCHEMA IF EXISTS analytics CASCADE;")
        cursor.execute("DROP SCHEMA IF EXISTS truth CASCADE;")
        for path in sql_files:
            cursor.execute(path.read_text(encoding="utf-8"))
            print(f"  applied {path.name}")
    connection.commit()


def preflight(connection, name: str, frame) -> tuple[list[str], list[str]]:
    """Diff a frame against the DDL BEFORE copying it.

    Decision A44. Three of the five defects the first live load found were
    columns the schema declared and the generator never populated. COPY reports
    the first such column as a NOT NULL violation and says nothing at all about
    the nullable ones -- which is how ``pit_days_since_last_order``, a
    WHITELISTED risk-model feature, reached the schema permanently empty.

    Columns with a server-side default are exempt: the three SERIAL surrogate
    keys are supposed to be absent from the frame.
    """
    schema = "truth" if name in TRUTH_TABLES else "analytics"
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, column_default FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position;",
            (schema, name))
        columns = cursor.fetchall()

    problems, notes = [], []
    missing = [c for c, default in columns
               if c not in frame.columns and default is None]
    if missing:
        problems.append(f"declared but absent from the frame: {missing}")
    for column, _ in columns:
        if column not in frame.columns or not frame[column].isna().all():
            continue
        reason = KNOWN_EMPTY.get((name, column))
        if reason is None:
            problems.append(f"present but 100% NULL -- never populated: {column}")
        else:
            notes.append(f"{column} is entirely NULL -- {reason}")
    return problems, notes


def copy_table(connection, name: str, frame) -> int:
    """COPY one frame in, then return the count the SERVER reports.

    Deliberately not ``len(frame)``: the point of the check is to compare what
    was sent against what actually landed.
    """
    import pandas as pd

    schema = "truth" if name in TRUTH_TABLES else "analytics"
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT column_name, data_type FROM information_schema.columns "
                       f"WHERE table_schema='{schema}' AND table_name='{name}' "
                       f"ORDER BY ordinal_position;")
        db_types = dict(cursor.fetchall())

    columns = [c for c in db_types if c in frame.columns]
    payload = frame[columns].copy()

    # A nullable integer column arrives from pandas as float64, because NaN is a
    # float — so it serialises as "4.0", which SMALLINT rejects. Cast against the
    # DB's own declared type rather than guessing from the dtype: the database is
    # the authority on what each column is.
    integer_types = {"smallint", "integer", "bigint"}
    for column in columns:
        if db_types[column] in integer_types and payload[column].dtype.kind == "f":
            payload[column] = payload[column].astype("Int64")
        elif db_types[column] == "boolean" and payload[column].dtype == object:
            payload[column] = payload[column].astype("boolean")

    buffer = io.StringIO()
    payload.to_csv(buffer, index=False, header=False, na_rep="\\N")
    buffer.seek(0)

    with connection.cursor() as cursor:
        cursor.copy_expert(
            f"COPY {schema}.{name} ({', '.join(columns)}) "
            "FROM STDIN WITH (FORMAT csv, NULL '\\N')",
            buffer,
        )
        cursor.execute(f"SELECT count(*) FROM {schema}.{name};")
        return int(cursor.fetchone()[0])


def load(env: dict) -> int:
    import pandas as pd

    sql_files = sorted(p for p in SQL_DIR.glob("*.sql") if p.parent == SQL_DIR)
    parquet = {p.stem: p for p in DATA_DIR.glob("*.parquet")}
    missing = [t for t in LOAD_ORDER if t not in parquet]
    if missing:
        print(f"ERROR: no parquet for {missing}. Run scripts/01_generate.py first.")
        return 2

    connection = connect(env)
    connection.autocommit = False
    print("applying DDL (drop and recreate — idempotent):")
    apply_ddl(connection, sql_files)

    print("")
    print("pre-flight (decision A44): frame vs DDL")
    defects, notes, frames = [], [], {}
    for name in LOAD_ORDER:
        frames[name] = pd.read_parquet(parquet[name])
        found, registered = preflight(connection, name, frames[name])
        partial_problems, partial_notes = check_partial(name, frames[name])
        defects.extend(f"   {name}: {problem}"
                       for problem in found + partial_problems)
        notes.extend(f"   KNOWN GAP  {name}.{note}" for note in registered)
        notes.extend(f"   BY DESIGN  {name}.{note}" for note in partial_notes)
    if defects:
        for line in defects:
            print(line)
        print("")
        print("ERROR: a declared column that is absent, or present and "
              "entirely NULL, is a GENERATOR defect. The parquet layer "
              "enforces neither. Fix the generator, do not relax this.")
        connection.close()
        return 1
    for note in notes:
        print(note)
    print(f"   {len(LOAD_ORDER)} tables: no absent columns, "
          f"0 unregistered all-NULL column(s)")
    print("\nCOPY:")
    print(f"   {'table':<34}{'parquet':>10}{'loaded':>10}   match")
    mismatches = []
    for name in LOAD_ORDER:
        frame = frames[name]
        loaded = copy_table(connection, name, frame)
        ok = loaded == len(frame)
        if not ok:
            mismatches.append((name, len(frame), loaded))
        print(f"   {name:<34}{len(frame):>10,}{loaded:>10,}   {'yes' if ok else 'NO'}")
    connection.commit()

    if mismatches:
        print("\nERROR: row-count mismatch. A short table satisfies every foreign "
              "key and fails silently forever.")
        for name, expected, actual in mismatches:
            print(f"   {name}: parquet {expected:,} vs loaded {actual:,}")
        connection.close()
        return 1

    total = sum(len(frames[t]) for t in LOAD_ORDER)
    print(f"\n{len(LOAD_ORDER)} tables, {total:,} rows. Every count matches.")
    connection.close()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="required: the load DROPS and recreates both schemas")
    args = parser.parse_args(argv)

    sql_files = sorted(p for p in SQL_DIR.glob("*.sql") if p.parent == SQL_DIR)
    if not sql_files:
        print(f"No .sql in {SQL_DIR}")
        return 2

    if args.dry_run:
        return dry_run(sql_files)
    if not args.force:
        print("Refusing to load without --force. The load DROPS both schemas.")
        return 2
    return load(read_env())


if __name__ == "__main__":
    sys.exit(main())
