"""DQ-16 — the outcome-conditional column sweep (decision A46).

Why this exists
---------------
`attempt_delay_days` was published on 100% of returned orders and 0% of
delivered ones for the whole of Phase 2, and all 68 other checks passed
throughout. The reason none of them caught it is that they each ask about **one
column they already know about**. Nothing asked the generic question.

That shape — *a column whose availability depends on the outcome it would be
used to explain* — is mechanically detectable. So it is detected mechanically,
across every order-linked column, rather than one defect at a time.

How it fails safe
-----------------
The check is an **allowlist**, not a heuristic. Legitimately outcome-conditional
columns (an RTO reason cannot exist on a delivered order) are declared in
`params.yaml` under `dq16_expected_outcome_conditional`. Anything flagged that
is *not* on the list fails the test.

So the mechanism that would have caught A46 is: a new column appears on the
flagged list, is not declared, and the suite goes red until someone decides
which of the three it is. **The check is the declaration, not the discovery.**

Scope
-----
Only tables that can be resolved to an order outcome are swept. Dimension
tables are excluded with reason: a product's rating is not a per-order fact, so
its "non-null rate by rto_flag" would measure the join, not the column.

Event-grain tables are collapsed to order grain first, asking "does this order
have this information available at all" — which is the question a downstream
consumer actually asks.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# A column is flagged when one arm is essentially always populated and the other
# essentially never is. The thresholds are deliberately extreme: this is looking
# for structural availability, not for a correlation.
POPULATED = 0.99
ABSENT = 0.01

# Keys that carry no information about availability -- they are always present
# by construction and would flag on nothing.
SKIP_COLUMNS = {"order_id", "session_id", "customer_id",
                "delivery_event_id", "checkout_event_id", "payment_attempt_id"}

# Excluded, with reason: no order grain, so the partition is undefined.
DIMENSION_TABLES = {"dim_date", "dim_geography", "dim_seller", "dim_product",
                    "dim_customer", "truth_customer_latent",
                    "truth_order_probability"}


def partitions(orders: pd.DataFrame) -> dict[str, tuple]:
    """The five outcome splits, each on its own honest base population.

    Each partition names the population it is defined over. Comparing
    ``is_delivered`` across *all* orders would flag every column that is NULL on
    a cancelled order, which is a different (and already-understood) fact.
    """
    shipped = orders["is_shipped"].to_numpy(bool)
    censored = orders["is_censored"].to_numpy(bool)
    resolved = shipped & ~censored
    rto = orders["rto_flag"].fillna(False).to_numpy(bool)
    delivered = orders["is_delivered"].fillna(False).to_numpy(bool)
    cod = (orders["payment_method"] == "COD").to_numpy(bool)
    ids = orders["order_id"].to_numpy()
    return {
        "rto_flag": (ids[resolved & rto], ids[resolved & ~rto], "returned", "not returned"),
        "is_delivered": (ids[resolved & delivered], ids[resolved & ~delivered],
                         "delivered", "not delivered"),
        "is_shipped": (ids[shipped], ids[~shipped], "shipped", "not shipped"),
        "is_censored": (ids[shipped & censored], ids[shipped & ~censored],
                        "censored", "not censored"),
        "payment_method": (ids[cod], ids[~cod], "COD", "PREPAID"),
    }


def order_level_availability(frame: pd.DataFrame, order_ids: pd.Series
                             ) -> pd.DataFrame:
    """Collapse any grain to order grain: does this order have this value at all?

    For an order-grain table this is the identity. For an event-grain table it is
    an OR across the order's events, which is the question a consumer asks.
    """
    columns = [c for c in frame.columns if c not in SKIP_COLUMNS]
    available = frame[columns].notna()
    available.insert(0, "order_id", order_ids.to_numpy())
    return available.groupby("order_id").max()


def sweep(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return one row per (table, column, partition) that is outcome-conditional."""
    orders = tables["fct_order"]
    parts = partitions(orders)

    # Resolve each table to order_id. Session-grain tables reach an order only
    # through a converted session; sessions that never ordered have no outcome
    # and are dropped rather than counted as missing.
    session_to_order = (orders[["session_id", "order_id"]]
                        .drop_duplicates("session_id").set_index("session_id")["order_id"])

    resolved_tables: dict[str, pd.DataFrame] = {}
    for name, frame in tables.items():
        if name in DIMENSION_TABLES or frame is None or frame.empty:
            continue
        if "order_id" in frame.columns:
            resolved_tables[name] = order_level_availability(frame, frame["order_id"])
        elif "session_id" in frame.columns:
            mapped = frame["session_id"].map(session_to_order)
            keep = mapped.notna()
            if not keep.any():
                continue
            resolved_tables[name] = order_level_availability(
                frame.loc[keep], mapped.loc[keep])

    rows = []
    for table, availability in resolved_tables.items():
        for partition, (ids_a, ids_b, label_a, label_b) in parts.items():
            in_a = availability.reindex(ids_a)
            in_b = availability.reindex(ids_b)
            if len(in_a) == 0 or len(in_b) == 0:
                continue
            for column in availability.columns:
                rate_a = float(in_a[column].fillna(False).mean())
                rate_b = float(in_b[column].fillna(False).mean())
                hit = ((rate_a >= POPULATED and rate_b <= ABSENT)
                       or (rate_b >= POPULATED and rate_a <= ABSENT))
                if not hit:
                    continue
                rows.append({
                    # The declaration unit is (column, PARTITION), not column.
                    # A46's own fix proves why: after the fix,
                    # attempt_delay_days is still legitimately absent on
                    # CENSORED orders, but must never again be absent on
                    # DELIVERED ones. Allowlisting the bare column would excuse
                    # both and re-open the defect it was written to close.
                    "declaration": f"{table}.{column}@{partition}",
                    "key": f"{table}.{column}",
                    "table": table, "column": column, "partition": partition,
                    "present_in": label_a if rate_a >= POPULATED else label_b,
                    "absent_in": label_b if rate_a >= POPULATED else label_a,
                    "rate_present": max(rate_a, rate_b),
                    "rate_absent": min(rate_a, rate_b),
                    "n_present": len(ids_a) if rate_a >= POPULATED else len(ids_b),
                    "n_absent": len(ids_b) if rate_a >= POPULATED else len(ids_a),
                })
    if not rows:
        return pd.DataFrame(columns=["declaration", "key", "table", "column", "partition",
                                     "present_in", "absent_in", "rate_present",
                                     "rate_absent", "n_present", "n_absent"])
    return pd.DataFrame(rows).sort_values("declaration", ignore_index=True)


def undeclared(flagged: pd.DataFrame, allowlist) -> list[str]:
    """Flagged keys not present in the declared allowlist. These fail DQ-16."""
    declared = set(allowlist or ())
    return sorted(set(flagged["declaration"]) - declared) if len(flagged) else []


def unused_declarations(flagged: pd.DataFrame, allowlist) -> list[str]:
    """Declared keys that no longer flag.

    Reported, never failed. A column can stop being outcome-conditional for a
    good reason -- A46 is exactly that -- and the allowlist should then shrink.
    Failing on it would punish the fix.
    """
    seen = set(flagged["declaration"]) if len(flagged) else set()
    return sorted(set(allowlist or ()) - seen)
