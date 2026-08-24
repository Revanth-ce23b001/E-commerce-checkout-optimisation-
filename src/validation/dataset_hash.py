"""One canonical content hash for ``fct_order``, used by two callers.

DQ-01 hashes the dataset, and the database-backed checks record which dataset
they ran against. Both must agree on what "the hash" means or the staleness
guard is worthless, so the definition lives here and nowhere else.

Deliberately **not** a hash of the parquet bytes: parquet embeds compression
choices and writer metadata, so two byte-different files can hold identical
data. Hashing a canonical CSV projection of the sorted frame makes the hash
answer "is this the same dataset?", which is the question being asked.
"""

from __future__ import annotations

import hashlib

import pandas as pd

NULL_TOKEN = "\\N"


def order_hash(frame: pd.DataFrame) -> str:
    payload = (
        frame.sort_values("order_id", kind="stable")
        .to_csv(index=False, na_rep=NULL_TOKEN)
        .encode("utf-8")
    )
    return hashlib.sha256(payload).hexdigest()
