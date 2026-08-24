"""Shared PostgreSQL connection settings.

Kept out of the loader and the verifier so both read the same source and cannot
drift apart. Credentials come from ``config/database.env``, which is gitignored;
``config/database.env.example`` is the committed template.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = REPO_ROOT / "config" / "database.env"


def read_env() -> dict[str, str]:
    """Connection settings, with shell ``PG*`` variables taking precedence."""
    settings = {
        "PGHOST": "localhost",
        "PGPORT": "5434",
        "PGUSER": "postgres",
        "PGDATABASE": "checkout_rto",
        "PGPASSWORD": "",
    }
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                settings[key.strip()] = value.strip()
    settings.update({k: v for k, v in os.environ.items() if k.startswith("PG")})
    return settings


def connect(env: dict, user: str | None = None, password: str | None = None):
    """Open a connection, optionally AS a different role.

    The ``user`` override exists for LK-05: the only way to test an enforced
    permission boundary is to log in as the role the boundary applies to.
    """
    import psycopg2

    return psycopg2.connect(
        host=env["PGHOST"],
        port=int(env["PGPORT"]),
        user=user or env["PGUSER"],
        password=password if password is not None else env.get("PGPASSWORD", ""),
        dbname=env["PGDATABASE"],
    )
