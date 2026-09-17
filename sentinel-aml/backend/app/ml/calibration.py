"""Probability calibrators (module-level classes so artifacts unpickle reliably)."""
from __future__ import annotations

import numpy as np
from sklearn.linear_model import LogisticRegression


class PlattCalibrator:
    """Sigmoid calibration on the logit of the raw score. Strictly monotonic -> ranking (ROC/PR-AUC) preserved,
    and only 2 parameters -> stable on small calibration sets, unlike isotonic regression."""

    def __init__(self):
        self.lr = LogisticRegression(C=1e6, max_iter=1000)

    @staticmethod
    def _logit(p):
        p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
        return np.log(p / (1 - p)).reshape(-1, 1)

    def fit(self, raw, y):
        self.lr.fit(self._logit(raw), y)
        return self

    def predict(self, raw):
        return self.lr.predict_proba(self._logit(raw))[:, 1]
