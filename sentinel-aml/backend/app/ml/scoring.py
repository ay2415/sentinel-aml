"""Online scoring service with per-prediction lineage (model version, explanation, latency)."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import numpy as np
from sqlalchemy.orm import Session
from xgboost import DMatrix

from app.database.models import Alert, Prediction
from app.ml import registry
from app.observability import metrics as m

log = logging.getLogger(__name__)
MODEL_NAME = "alert_risk"


@dataclass
class ScoreResult:
    score: float
    raw_score: float
    anomaly_score: float
    above_threshold: bool
    threshold: float
    top_contributors: list[dict]
    model_version_id: str
    model_version: str
    latency_ms: float


class ModelService:
    def __init__(self):
        self._lock = threading.Lock()
        self._loaded: tuple[str, dict, str] | None = None  # (model_version_id, artifact, version)

    def _ensure(self, db: Session) -> tuple[str, dict, str]:
        mv = registry.get_production(db, MODEL_NAME)
        if mv is None:
            raise RuntimeError("no production model registered; run `make train`")
        with self._lock:
            if self._loaded is None or self._loaded[0] != mv.id:  # hot-swap on promotion
                self._loaded = (mv.id, registry.load_verified(mv), mv.version)
        return self._loaded

    def threshold(self, db: Session) -> float:
        return float(self._ensure(db)[1]["threshold"])

    def version_info(self, db: Session) -> dict:
        mv_id, art, version = self._ensure(db)
        return {"model_version_id": mv_id, "version": version, "threshold": float(art["threshold"])}

    def is_ready(self, db: Session) -> bool:
        try:
            self._ensure(db)
            return True
        except Exception:
            return False

    def score_features(self, db: Session, features: dict, rule_code: str) -> ScoreResult:
        t0 = time.perf_counter()
        mv_id, art, version = self._ensure(db)
        base = [float(features.get(k, 0.0)) for k in art["base_features"]]
        x = np.asarray([base + [1.0 if rule_code == c else 0.0 for c in art["rule_codes"]]], dtype=float)
        raw = float(art["xgb"].predict_proba(x)[0, 1])
        cal = float(art["calibrator"].predict([raw])[0])
        anom_raw = float(-art["iforest"].score_samples(x[:, : len(art["base_features"])])[0])
        anomaly_pct = float(np.searchsorted(art["iforest_train_scores"], anom_raw) / len(art["iforest_train_scores"]))
        contribs = art["xgb"].get_booster().predict(DMatrix(x, feature_names=art["features"]), pred_contribs=True)[0][:-1]
        order = np.argsort(-np.abs(contribs))[:6]
        top = [{"feature": art["features"][i], "value": round(float(x[0, i]), 3), "contribution": round(float(contribs[i]), 4)}
               for i in order]
        latency = (time.perf_counter() - t0) * 1000
        m.ML_PREDICTIONS.labels(model_version=version).inc()
        m.ML_SCORE.observe(cal)
        m.ML_LATENCY.observe(latency / 1000)
        return ScoreResult(cal, raw, anomaly_pct, cal >= art["threshold"], art["threshold"], top, mv_id, version, latency)

    def score_alert(self, db: Session, alert: Alert) -> ScoreResult:
        r = self.score_features(db, alert.features, alert.rule_code)
        alert.risk_score, alert.anomaly_score, alert.model_version_id = r.score, r.anomaly_score, r.model_version_id
        alert.priority = priority_from(r.score, r.anomaly_score, r.threshold)
        db.add(Prediction(tenant_id=alert.tenant_id, alert_id=alert.id, model_version_id=r.model_version_id, score=r.score,
                          raw_score=r.raw_score, anomaly_score=r.anomaly_score, above_threshold=r.above_threshold,
                          top_contributors=r.top_contributors, latency_ms=r.latency_ms))
        return r


def priority_from(score: float, anomaly: float, threshold: float):
    from app.database.models import Priority

    if score >= 0.7:
        return Priority.CRITICAL
    if score >= 0.4 or anomaly >= 0.99:
        return Priority.HIGH
    if score >= threshold:
        return Priority.MEDIUM
    return Priority.LOW


model_service = ModelService()
