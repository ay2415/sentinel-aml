"""Evaluation metrics tailored to alert triage (imbalanced, capacity-constrained)."""
from __future__ import annotations

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)


def expected_calibration_error(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    ece = 0.0
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (p >= lo) & (p < hi) if hi < 1 else (p >= lo) & (p <= hi)
        if m.any():
            ece += m.mean() * abs(y[m].mean() - p[m].mean())
    return float(ece)


def threshold_for_recall(y: np.ndarray, p: np.ndarray, target_recall: float) -> float:
    """Highest threshold whose recall on this set is >= target (choose on validation, never on test)."""
    pos = np.sort(p[y == 1])
    if pos.size == 0:
        return 0.5
    k = int(np.floor((1 - target_recall) * pos.size))
    return float(pos[min(k, pos.size - 1)])


def classification_report(y: np.ndarray, p: np.ndarray, threshold: float) -> dict:
    yhat = (p >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, yhat, labels=[0, 1]).ravel()
    order = np.argsort(-p)
    top10 = order[: max(1, int(0.1 * len(p)))]
    return {
        "n": int(len(y)), "positives": int(y.sum()), "prevalence": float(y.mean()),
        "roc_auc": float(roc_auc_score(y, p)) if 0 < y.sum() < len(y) else None,
        "pr_auc": float(average_precision_score(y, p)) if y.sum() else None,
        "threshold": float(threshold),
        "precision": float(precision_score(y, yhat, zero_division=0)), "recall": float(recall_score(y, yhat, zero_division=0)),
        "f1": float(f1_score(y, yhat, zero_division=0)),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "brier": float(brier_score_loss(y, p)), "ece": expected_calibration_error(y, p),
        "precision_at_top10pct": float(y[top10].mean()),
        # business metric: share of alerts that fall below threshold and can be routed to a lighter-touch queue
        "workload_deprioritised_share": float((yhat == 0).mean()),
    }
