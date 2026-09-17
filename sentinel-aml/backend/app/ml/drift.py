"""Data/prediction drift monitoring with the Population Stability Index (PSI).

PSI < 0.10 stable | 0.10-0.25 investigate | > 0.25 significant -> retraining review.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BINS = 10


def _edges(x: np.ndarray) -> list[float]:
    qs = np.unique(np.quantile(x, np.linspace(0, 1, BINS + 1)))
    return qs.tolist() if len(qs) > 2 else [float(x.min()) - 1e-9, float(x.max()) + 1e-9]


def _hist(x: np.ndarray, edges: list[float]) -> list[float]:
    inner = np.asarray(edges[1:-1])
    idx = np.searchsorted(inner, x, side="right")
    counts = np.bincount(idx, minlength=len(edges) - 1).astype(float)
    return (counts / max(1, counts.sum())).tolist()


def reference_profile(X: pd.DataFrame, scores: np.ndarray) -> dict:
    prof = {}
    for c in X.columns:
        e = _edges(X[c].to_numpy())
        prof[c] = {"edges": e, "dist": _hist(X[c].to_numpy(), e)}
    e = _edges(scores)
    prof["__score__"] = {"edges": e, "dist": _hist(scores, e)}
    return prof


def psi(expected: list[float], actual: list[float], eps: float = 1e-4) -> float:
    e = np.clip(np.asarray(expected), eps, None)
    a = np.clip(np.asarray(actual), eps, None)
    return float(np.sum((a - e) * np.log(a / e)))


def drift_report(profile: dict, current: pd.DataFrame, current_scores: np.ndarray | None = None) -> dict:
    out = {}
    for c, ref in profile.items():
        if c == "__score__":
            if current_scores is not None and len(current_scores):
                out[c] = psi(ref["dist"], _hist(current_scores, ref["edges"]))
            continue
        if c in current:
            out[c] = psi(ref["dist"], _hist(current[c].to_numpy(dtype=float), ref["edges"]))
    worst = sorted(out.items(), key=lambda t: -t[1])[:8]
    max_psi = max(out.values()) if out else 0.0
    status = "stable" if max_psi < 0.10 else ("investigate" if max_psi < 0.25 else "significant_drift")
    return {"status": status, "max_psi": max_psi, "top_features": [{"feature": k, "psi": round(v, 4)} for k, v in worst],
            "n_current": int(len(current))}
