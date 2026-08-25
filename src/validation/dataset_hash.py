"""Canonical content hashes for the dataset and for the schema.

DQ-01 hashes the dataset, and the database-backed checks record which dataset
they ran against. Both must agree on what "the hash" means or the staleness
guard is worthless, so the definition lives here and nowhere else.

Deliberately **not** a hash of the parquet bytes: parquet embeds compression
choices and writer metadata, so two byte-different files can hold identical
data. Hashing a canonical CSV projection of the sorted frame makes the hash
answer "is this the same dataset?", which is the question being asked.

The DDL is hashed separately, and the reason is a lesson from decision A45.
A45 added a column and two CHECK constraints while leaving ``fct_order``
byte-identical -- that inertness was the whole point of the change. A staleness
guard keyed only on the data hash would therefore have accepted a
``database_checks.json`` describing the OLD schema as current, and reported a
PASS about 102 constraints that are now 104. LK-01 is worse: it is a statement
about a VIEW, and the view lives entirely in the DDL, so a data hash says nothing
at all about whether the result is still true.

Two independent things can go stale here. Both are hashed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"

NULL_TOKEN = "\\N"


def order_hash(frame: pd.DataFrame) -> str:
    payload = (
        frame.sort_values("order_id", kind="stable")
        .to_csv(index=False, na_rep=NULL_TOKEN)
        .encode("utf-8")
    )
    return hashlib.sha256(payload).hexdigest()


def ddl_hash(sql_dir: Path = SQL_DIR) -> str:
    """SHA-256 of every ``sql/*.sql`` file, in name order.

    Answers "is this the same schema?" -- the question a database-backed check
    result depends on just as much as it depends on the data. Hashing the files
    rather than ``pg_catalog`` keeps the definition usable without a server, and
    keeps it in the same place as the data hash so the two cannot drift apart.
    """
    digest = hashlib.sha256()
    for path in sorted(sql_dir.glob("*.sql")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()
