"""Discrimination, calibration and lift.

Calibration is the headline here, not AUC. The intervention thresholds are
absolute probabilities tied to money (p* = 0.2576, derived), so a model that
ranks well but reports 0.31 where the truth is 0.19 will restrict orders that
are in fact profitable. Blueprint §9.3: "A model with AUC 0.78 and good
calibration is more useful than AUC 0.83 with poor calibration."

Three calibration numbers, because they fail in different ways:

* **Brier score** — overall squared error. Moves with both calibration and
  discrimination, so it is a summary, not a diagnosis.
* **ECE** (expected calibration error) — mean |predicted − observed| across
  equal-count bins, weighted by bin size. This is the one that answers
  "if the model says 26%, does 26% of that group actually RTO?"
* **Calibration slope and intercept** — a logistic regression of the outcome on
  the model's own logit. Slope 1.0 / intercept 0.0 is perfect. Slope < 1 means
  the scores are too spread out; slope > 1 means too compressed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def auc(y: np.ndarray, p: np.ndarray) -> float:
    """Rank-based AUC. Equal to the Mann-Whitney U statistic, ties averaged."""
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p), dtype=float)
    sorted_p = p[order]
    i = 0
    while i < len(sorted_p):
        j = i
        while j + 1 < len(sorted_p) and sorted_p[j + 1] == sorted_p[i]:
            j += 1
        ranks[order[i:j + 1]] = 0.5 * (i + j) + 1.0
        i = j + 1
    n_pos, n_neg = y.sum(), len(y) - y.sum()
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    return float((ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def ks(y: np.ndarray, p: np.ndarray) -> float:
    """Kolmogorov-Smirnov separation — max gap between the two CDFs."""
    frame = pd.DataFrame({"y": np.asarray(y, float), "p": np.asarray(p, float)})
    frame = frame.sort_values("p")
    pos = (frame["y"] == 1).cumsum() / max(frame["y"].sum(), 1)
    neg = (frame["y"] == 0).cumsum() / max((1 - frame["y"]).sum(), 1)
    return float((neg - pos).abs().max())


def reliability(y: np.ndarray, p: np.ndarray, bins: int = 10) -> pd.DataFrame:
    """Equal-count bins. Equal-WIDTH bins put 90% of the mass in two buckets."""
    frame = pd.DataFrame({"y": np.asarray(y, float), "p": np.asarray(p, float)})
    frame["bin"] = pd.qcut(frame["p"].rank(method="first"), bins, labels=False)
    grouped = frame.groupby("bin").agg(
        n=("y", "size"), predicted=("p", "mean"), observed=("y", "mean"),
        p_low=("p", "min"), p_high=("p", "max"))
    grouped["gap_pp"] = (grouped["observed"] - grouped["predicted"]) * 100
    return grouped.reset_index(drop=True)


def ece(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    table = reliability(y, p, bins)
    weight = table["n"] / table["n"].sum()
    return float((weight * (table["observed"] - table["predicted"]).abs()).sum())


def calibration_line(y: np.ndarray, p: np.ndarray) -> tuple[float, float]:
    """Slope and intercept of outcome ~ logit(prediction). (1.0, 0.0) is perfect."""
    import statsmodels.api as sm

    eps = 1e-9
    logit = np.log(np.clip(p, eps, 1 - eps) / np.clip(1 - p, eps, 1 - eps))
    design = sm.add_constant(logit)
    fit = sm.Logit(np.asarray(y, float), design).fit(disp=0)
    return float(fit.params[1]), float(fit.params[0])


def decile_lift(y: np.ndarray, p: np.ndarray, bins: int = 10) -> pd.DataFrame:
    """Risk deciles, highest first. The table a risk committee actually reads."""
    table = reliability(y, p, bins).iloc[::-1].reset_index(drop=True)
    base = np.asarray(y, float).mean()
    table.insert(0, "decile", np.arange(1, len(table) + 1))
    table["lift"] = table["observed"] / base
    table["captured_pct"] = (table["observed"] * table["n"]).cumsum() / (base * table["n"].sum()) * 100
    return table


def summarise(y: np.ndarray, p: np.ndarray, label: str) -> dict:
    slope, intercept = calibration_line(y, p)
    return {
        "model": label,
        "n": int(len(y)),
        "base_rate": round(float(np.mean(y)), 4),
        "auc": round(auc(y, p), 4),
        "ks": round(ks(y, p), 4),
        "brier": round(brier(y, p), 5),
        "ece": round(ece(y, p), 5),
        "cal_slope": round(slope, 4),
        "cal_intercept": round(intercept, 4),
        "mean_predicted": round(float(np.mean(p)), 4),
    }
