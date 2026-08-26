"""Logistic regression, and its translation into a points scorecard.

Blueprint §9.3 makes this the *primary* artefact, not a stepping stone to a GBM.
The reason is not nostalgia for logistic regression — it is that the coefficients
convert to points a support agent can read off a screen, and the probabilities
are calibrated by construction, which the economic thresholds require.

**No regularisation by default.** L2 shrinks coefficients toward zero, which
improves out-of-sample AUC marginally and degrades calibration systematically —
the wrong trade when the threshold is an absolute probability. If separation or
collinearity forces it, that is a finding to report, not a default to reach for.

Scorecard points use the industry-standard PDO transform: choose a reference
odds and a "points to double the odds", and each feature's contribution becomes
``-beta * (PDO / ln 2) * (x - mean) / sd``. The negative sign makes HIGHER points
mean LOWER risk, which is the convention every credit scorecard uses and the one
a support agent will already expect.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


class Scorecard:
    def __init__(self, alpha: float = 0.0):
        self.alpha = alpha
        self.fit_ = None
        self.columns_: list[str] = []

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> "Scorecard":
        import statsmodels.api as sm

        self.columns_ = list(X.columns)
        design = sm.add_constant(X.to_numpy(float), has_constant="add")
        model = sm.Logit(np.asarray(y, float), design)
        if self.alpha > 0:
            self.fit_ = model.fit_regularized(alpha=self.alpha, L1_wt=0.0, disp=0)
        else:
            self.fit_ = model.fit(disp=0, maxiter=200)
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        import statsmodels.api as sm

        aligned = X.reindex(columns=self.columns_, fill_value=0.0)
        design = sm.add_constant(aligned.to_numpy(float), has_constant="add")
        return np.asarray(self.fit_.predict(design), dtype=float)

    def coefficients(self) -> pd.DataFrame:
        """Per-standard-deviation coefficients, largest absolute effect first.

        Because the design matrix is standardised, |coefficient| IS the feature
        importance: the log-odds movement from a one-SD change, holding the rest
        fixed. No permutation importance needed, and no tree-based importance to
        misread.
        """
        params = np.asarray(self.fit_.params, dtype=float)
        frame = pd.DataFrame({
            "feature": ["(intercept)"] + self.columns_,
            "coef_per_sd": params,
        })
        if hasattr(self.fit_, "bse"):
            bse = np.asarray(self.fit_.bse, dtype=float)
            frame["std_err"] = bse
            frame["z"] = params / np.where(bse == 0, np.nan, bse)
            frame["p_value"] = np.asarray(self.fit_.pvalues, dtype=float)
        frame["odds_ratio_per_sd"] = np.exp(params)
        frame["abs_coef"] = frame["coef_per_sd"].abs()
        body = frame[frame["feature"] != "(intercept)"].sort_values(
            "abs_coef", ascending=False)
        return pd.concat([frame[frame["feature"] == "(intercept)"], body]).drop(
            columns="abs_coef").reset_index(drop=True)

    def points(self, pdo: float = 20.0, base_points: float = 600.0,
               base_odds: float = 20.0) -> pd.DataFrame:
        """Points per one-SD move. Higher points = safer, the usual convention."""
        factor = pdo / np.log(2.0)
        coefs = self.coefficients()
        body = coefs[coefs["feature"] != "(intercept)"].copy()
        body["points_per_sd"] = -body["coef_per_sd"] * factor
        header = pd.DataFrame([{
            "feature": f"(base score at odds {base_odds:.0f}:1)",
            "points_per_sd": base_points,
        }])
        return pd.concat([header, body[["feature", "points_per_sd", "coef_per_sd"]]],
                         ignore_index=True)

    @property
    def pseudo_r2(self) -> float:
        return float(getattr(self.fit_, "prsquared", float("nan")))
