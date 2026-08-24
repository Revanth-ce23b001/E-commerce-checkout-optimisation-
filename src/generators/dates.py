"""Module 02 — ``dim_date``. The 90-day calendar spine.

Business concept
----------------
Two calendar facts in this table are load-bearing rather than decorative.

``is_month_end_window`` (day 26 onward) carries the **salary-cycle liquidity
effect**: it enters the COD logit at +0.14, and — more importantly — it enters
the RTO logit as an *interaction* with COD at +0.30. That interaction is the
mechanism behind BR-10, the claim that month-end COD orders fail more often
because the cash is not there when the courier knocks. A flat calendar would make
that hypothesis untestable.

``demand_index`` shapes session volume so that the 90-day window has realistic
weekly rhythm rather than uniform traffic. It is the only column here that is not
a pure function of the date.

Spec references
---------------
- Spec §3.1  — the column list
- Spec §13.2 — window_start, window_days
- Blueprint  — the salary-cycle mechanism behind R20 / BR-10

⚠️ ``distributions.demand`` is [A27 PROPOSED] and not yet approved.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
from numpy.random import Generator

MONTH_END_FIRST_DAY = 26      # spec §3.1: is_month_end_window = day_of_month >= 26
SALARY_WEEK_LAST_DAY = 7      # spec §3.1: is_salary_week = day_of_month <= 7


def generate_dates(params, rng: Generator) -> pd.DataFrame:
    """Build the calendar spine.

    Parameters
    ----------
    params
        Loaded :class:`~src.config.loader.Params`.
    rng
        The ``date`` substream. Used only for the demand-index noise term.
    """
    window_start = _as_date(params.require("meta.window_start"))
    window_days = int(params.require("meta.window_days"))
    demand = params.require("distributions.demand")

    dates = [window_start + dt.timedelta(days=i) for i in range(window_days)]
    frame = pd.DataFrame({"date_id": dates})

    frame["day_index"] = np.arange(window_days, dtype=np.int64)
    frame["day_of_week"] = frame["date_id"].map(lambda d: d.isoweekday()).astype(np.int16)
    frame["is_weekend"] = frame["day_of_week"] >= 6
    frame["day_of_month"] = frame["date_id"].map(lambda d: d.day).astype(np.int16)
    frame["is_month_end_window"] = frame["day_of_month"] >= MONTH_END_FIRST_DAY
    frame["is_salary_week"] = frame["day_of_month"] <= SALARY_WEEK_LAST_DAY

    frame["demand_index"] = _demand_index(frame, demand, rng)

    return frame


def _demand_index(
    frame: pd.DataFrame, demand: dict, rng: Generator
) -> np.ndarray:
    """Multiplicative demand: weekday rhythm x month-end x salary week x noise.

    Multiplicative rather than additive so the effects compound the way real
    traffic does, and so the index cannot go negative.
    """
    weekday_map = {int(k): float(v) for k, v in demand["weekday_multiplier"].items()}
    missing = set(frame["day_of_week"].unique().tolist()) - set(weekday_map)
    if missing:
        raise ValueError(
            f"distributions.demand.weekday_multiplier has no entry for ISO weekday(s) "
            f"{sorted(missing)}. Every observed level needs an explicit value."
        )

    index = frame["day_of_week"].map(weekday_map).to_numpy(dtype=np.float64)
    index = np.where(frame["is_month_end_window"], index * float(demand["month_end_multiplier"]), index)
    index = np.where(frame["is_salary_week"], index * float(demand["salary_week_multiplier"]), index)

    noise = rng.normal(0.0, float(demand["noise_sd"]), size=len(frame))
    index = index * (1.0 + noise)

    # A non-positive demand index would mean a day with no traffic, which is not
    # a thing this marketplace does. Clip rather than allow it silently.
    return np.clip(index, 0.05, None).round(4)


def _as_date(value) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value))
