"""Load the generated dataset into PostgreSQL — or, with ``--dry-run``, check the
DDL without a live connection.

Why --dry-run exists
--------------------
The schema is the contract, and it was written before any generator code. But a
schema nobody can parse is not a contract — it is a hope. Until PostgreSQL is
installed on the build machine, ``--dry-run`` parses every statement in
``sql/*.sql`` with sqlglot's postgres dialect and reports failures with the
offending statement. It catches structural errors (unbalanced parens, bad
constraint syntax, misspelled types) at zero cost.

What it does NOT catch, stated plainly so nobody over-trusts it:
  * semantic errors — a REFERENCES pointing at a table that does not exist,
    a duplicate constraint name, a type mismatch across a foreign key
  * anything about the DATA
  * whether the REVOKE grants actually take effect (that is LK-05, which needs a
    live server and a real ``analyst`` role)

A clean dry-run means "this will parse", not "this will apply".

Usage
-----
    python scripts/02_load_postgres.py --dry-run
    python scripts/02_load_postgres.py --force        # real load; needs data + server
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = REPO_ROOT / "sql"


def split_statements(sql_text: str) -> list[str]:
    """Split a script into statements on semicolons.

    Three things must be skipped, and each one bit on the first attempt:

    * ``-- line comments``. This file's comments are prose, and prose contains
      semicolons ("Primary keys everywhere; foreign keys enforced..."). Splitting
      on those tears a CREATE TABLE in half and produces a page of parse errors
      that are artefacts of the splitter, not the DDL.
    * ``$$ ... $$`` dollar-quoted blocks. PostgreSQL DO blocks carry their own
      semicolons.
    * ``'single-quoted strings'``, which appear in every enum CHECK constraint.

    Comments are stripped from the returned statements as well as skipped during
    the scan, so what reaches the parser is the SQL and nothing else.
    """
    statements: list[str] = []
    current: list[str] = []
    in_dollar = False
    in_quote = False
    i = 0
    n = len(sql_text)

    while i < n:
        if not in_quote and sql_text.startswith("$$", i):
            in_dollar = not in_dollar
            current.append("$$")
            i += 2
            continue

        char = sql_text[i]

        # A line comment runs to the end of the line. Drop it entirely.
        if not in_quote and not in_dollar and sql_text.startswith("--", i):
            newline = sql_text.find("\n", i)
            i = n if newline == -1 else newline
            continue

        if char == "'" and not in_dollar:
            # '' inside a string is an escaped quote, not a terminator.
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


def first_line(statement: str) -> str:
    """The first non-comment line, for identifying a statement in the report."""
    for line in statement.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("--"):
            return stripped[:78]
    return statement[:78]


def dry_run(sql_files: list[Path]) -> int:
    """Parse every statement with sqlglot's postgres dialect. Returns an exit code."""
    try:
        import logging

        import sqlglot
        from sqlglot import expressions as exp
        from sqlglot.errors import ParseError
    except ImportError:
        print("ERROR: sqlglot is not installed. Install it, or run without --dry-run.")
        return 2

    # sqlglot logs a warning for every statement it parses as an opaque Command.
    # Those are counted and reported below, so the per-statement noise is muted.
    logging.getLogger("sqlglot").setLevel(logging.ERROR)

    total = 0
    failures: list[tuple[Path, str, str]] = []
    opaque: list[tuple[Path, str]] = []

    for path in sql_files:
        if not path.exists():
            print(f"SKIP  {path.name} — not found")
            continue
        statements = split_statements(path.read_text(encoding="utf-8"))
        file_failures = 0
        for statement in statements:
            total += 1
            try:
                parsed = sqlglot.parse_one(statement, dialect="postgres")
                if parsed is None:
                    raise ParseError("parsed to nothing")
                # sqlglot has no grammar for GRANT / REVOKE / ALTER DEFAULT
                # PRIVILEGES / DO, so it wraps them as an opaque Command rather
                # than failing. That is NOT a syntax check — say so.
                if isinstance(parsed, exp.Command):
                    opaque.append((path, first_line(statement)))
            except ParseError as exc:
                file_failures += 1
                failures.append((path, first_line(statement), str(exc).strip()))
        status = "OK  " if file_failures == 0 else "FAIL"
        print(f"{status}  {path.name:<28} {len(statements):>3} statement(s)"
              + ("" if file_failures == 0 else f" — {file_failures} failed"))

    print()
    if failures:
        print(f"{len(failures)} of {total} statement(s) failed to parse:\n")
        for path, line, message in failures:
            print(f"  {path.name}")
            print(f"    statement: {line}")
            print(f"    error:     {message}\n")
        return 1

    print(f"All {total} statement(s) parsed cleanly (postgres dialect).")

    if opaque:
        print()
        print(f"{len(opaque)} statement(s) were parsed as opaque Commands — sqlglot has")
        print("no grammar for them, so they were NOT syntax-checked:")
        for path, line in opaque:
            print(f"  {path.name}: {line}")
        print("These are the permission statements. LK-05 verifies them for real,")
        print("against a live server with a real analyst role.")

    print()
    print("NOTE: parsing is not validation. Semantic errors — missing referenced")
    print("      tables, duplicate constraint names, cross-table type mismatches —")
    print("      still need a live server.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse sql/*.sql with sqlglot and report errors. No connection needed.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Required for a real load: this drops and recreates both schemas.",
    )
    args = parser.parse_args(argv)

    sql_files = sorted(SQL_DIR.glob("*.sql"))
    if not sql_files:
        print(f"No .sql files found in {SQL_DIR}")
        return 2

    if args.dry_run:
        return dry_run(sql_files)

    raise NotImplementedError(
        "The live PostgreSQL load is generation module 22 and needs a dataset, which "
        "does not exist yet. Run with --dry-run to check the DDL parses."
    )


if __name__ == "__main__":
    sys.exit(main())
