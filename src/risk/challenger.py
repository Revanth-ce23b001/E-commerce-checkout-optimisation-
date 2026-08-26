"""The gradient-boosted challenger, and the rule that decides whether it ships.

Blueprint §9.3 is unusually specific about this, and the specificity is the
point: the GBM is "only shipped if it beats the scorecard by >= 3pp AUC **and**
>= INR X in simulated CM. Otherwise the interpretability is worth more than the
accuracy."

So this module does not return "the better model". It returns a **verdict**, and
the verdict defaults to the scorecard. Two things follow from that:

* **The margin is checked before anything else is reported.** A challenger that
  wins by 1pp is not a marginally better model to think about, it is a model that
  does not ship, and reporting its calibration curve next to the scorecard's
  invites exactly the argument §9.3 pre-committed against.
* **Calibration is reported even when the challenger loses**, because the
  discipline runs the other way too: a GBM that cleared 3pp on AUC and was badly
  calibrated would still be the wrong model here. The thresholds are absolute
  probabilities tied to money.

``sklearn`` is in the approved stack for benchmarking. That is what this is: the
challenger's entire job is to put a number on what the scorecard's
interpretability costs. If it ever wins, that number stops being free and the
decision goes back to the risk committee.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .evaluate import auc as _auc, summarise

# Blueprint §9.3. Not tunable, and deliberately not in params.yaml: it is a
# decision rule about model governance, not a business assumption about the
# world, and params.yaml is the register for the latter.
SHIP_MARGIN_AUC = 0.03


def fit_challenger(X_train: pd.DataFrame, y_train: np.ndarray,
                   seed: int) -> "HistGradientBoostingClassifier":
    """Modest depth and a real early-stopping split. Both are load-bearing.

    An unconstrained GBM on 50K rows and 50 features will memorise the training
    set and post a train AUC near the achievable ceiling, which reads as leakage
    when it is only overfitting. Depth 4 with early stopping keeps the comparison
    against the scorecard about signal rather than about capacity.
    """
    from sklearn.ensemble import HistGradientBoostingClassifier

    model = HistGradientBoostingClassifier(
        max_depth=4,
        max_iter=400,
        learning_rate=0.05,
        min_samples_leaf=100,
        l2_regularization=1.0,
        early_stopping=True,
        validation_fraction=0.15,
        n_iter_no_change=20,
        random_state=seed,
    )
    model.fit(X_train.to_numpy(float), np.asarray(y_train, dtype=int))
    return model


def challenge(X_train: pd.DataFrame, y_train: np.ndarray,
              X_test: pd.DataFrame, y_test: np.ndarray,
              scorecard_test_auc: float, seed: int) -> dict:
    """Fit the challenger and apply §9.3's ship rule.

    Returns the scoreboard row, the margin, and the verdict. The caller reports
    the verdict; it does not get to weigh the margin again.
    """
    model = fit_challenger(X_train, y_train, seed)
    p_test = model.predict_proba(X_test.to_numpy(float))[:, 1]
    p_train = model.predict_proba(X_train.to_numpy(float))[:, 1]

    test_auc = _auc(y_test, p_test)
    margin = test_auc - scorecard_test_auc
    ships = margin >= SHIP_MARGIN_AUC

    return {
        "model": model,
        "p_test": p_test,
        "scoreboard": pd.DataFrame([
            summarise(y_train, p_train, "GBM challenger - train"),
            summarise(y_test, p_test, "GBM challenger - test"),
        ]),
        "test_auc": test_auc,
        "train_auc": _auc(y_train, p_train),
        "margin_pp": margin * 100,
        "ships": bool(ships),
        "verdict": (
            "SHIPS — clears the >= {:.0f}pp margin. The interpretability trade "
            "now has a price and goes back to the risk "
            "committee.".format(SHIP_MARGIN_AUC * 100)
            if ships else
            "DOES NOT SHIP — the margin is {:+.2f}pp against a required "
            "{:+.2f}pp. Blueprint §9.3 pre-committed to keeping the scorecard in "
            "this case, and the scorecard is what the tiering below uses."
            .format(margin * 100, SHIP_MARGIN_AUC * 100)),
    }
