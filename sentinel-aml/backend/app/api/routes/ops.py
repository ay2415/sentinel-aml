"""MLOps / LLMOps visibility: model lineage, drift, prompt versions, token usage and cost."""
from datetime import timedelta

import pandas as pd
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.prompts import list_prompts
from app.api.deps import get_db
from app.core.errors import NotFoundError
from app.data.features import FEATURE_NAMES
from app.database.models import Alert, LLMCall, ModelVersion, Prediction, utcnow
from app.ml import registry
from app.ml.drift import drift_report
from app.security.auth import Principal, require
from app.security.rbac import Permission

router = APIRouter(prefix="/ops", tags=["mlops-llmops"])


@router.get("/models")
def models(p: Principal = Depends(require(Permission.READ_OPS)), db: Session = Depends(get_db)):
    rows = db.execute(select(ModelVersion).order_by(ModelVersion.created_at.desc())).scalars().all()
    out = []
    for mv in rows:
        cal = mv.metrics.get("xgboost_calibrated_test", {})
        out.append({"id": mv.id, "name": mv.name, "version": mv.version, "stage": mv.stage, "algorithm": mv.algorithm,
                    "artifact_sha256": mv.artifact_sha256[:16], "training_data_hash": mv.training_data_hash[:16], "created_at": mv.created_at,
                    "test_pr_auc": cal.get("pr_auc"), "test_roc_auc": cal.get("roc_auc"), "test_recall": cal.get("recall"),
                    "gates": mv.metrics.get("promotion_gates")})
    return out


@router.get("/predictions/{prediction_id}/lineage", summary="Which model produced this prediction?")
def lineage(prediction_id: str, p: Principal = Depends(require(Permission.READ_OPS)), db: Session = Depends(get_db)):
    row = db.execute(select(Prediction, ModelVersion).join(ModelVersion, ModelVersion.id == Prediction.model_version_id)
                     .where(Prediction.id == prediction_id, Prediction.tenant_id == p.tenant_id)).first()
    if not row:
        raise NotFoundError("prediction not found")
    pr, mv = row
    return {"prediction": {"id": pr.id, "alert_id": pr.alert_id, "score": pr.score, "created_at": pr.created_at},
            "model": {"name": mv.name, "version": mv.version, "algorithm": mv.algorithm, "artifact_uri": mv.artifact_uri,
                      "artifact_sha256": mv.artifact_sha256, "training_data_hash": mv.training_data_hash, "params": mv.params,
                      "feature_names": mv.feature_names}}


@router.get("/drift")
def drift(days: int = Query(30, ge=7, le=180), p: Principal = Depends(require(Permission.READ_OPS)), db: Session = Depends(get_db)):
    mv = registry.get_production(db, "alert_risk")
    if mv is None:
        raise NotFoundError("no production model")
    latest = db.execute(select(func.max(Alert.triggered_at)).where(Alert.tenant_id == p.tenant_id)).scalar_one()
    since = (latest or utcnow()) - timedelta(days=days)
    rows = db.execute(select(Alert.features, Prediction.raw_score).join(Prediction, Prediction.alert_id == Alert.id)
                      .where(Alert.tenant_id == p.tenant_id, Alert.triggered_at >= since, Prediction.model_version_id == mv.id)).all()
    cur = pd.DataFrame([r[0] for r in rows]).reindex(columns=FEATURE_NAMES).fillna(0.0)
    rep = drift_report(mv.metrics.get("reference_profile", {}), cur, pd.Series([r[1] for r in rows]).to_numpy())
    return {"model_version": mv.version, "window_days": days, **rep}


@router.get("/llm-usage")
def llm_usage(p: Principal = Depends(require(Permission.READ_OPS)), db: Session = Depends(get_db)):
    rows = db.execute(select(LLMCall.agent_name, LLMCall.model, LLMCall.prompt_name, LLMCall.prompt_version, func.count(),
                             func.sum(LLMCall.input_tokens), func.sum(LLMCall.output_tokens), func.sum(LLMCall.cost_usd),
                             func.avg(LLMCall.latency_ms), func.sum(func.cast(LLMCall.status != "ok", type_=func.count().type)))
                      .where(LLMCall.tenant_id == p.tenant_id)
                      .group_by(LLMCall.agent_name, LLMCall.model, LLMCall.prompt_name, LLMCall.prompt_version)).all()
    estimated = db.execute(select(func.bool_or(LLMCall.tokens_estimated) if db.bind.dialect.name == "postgresql"
                                  else func.max(LLMCall.tokens_estimated)).where(LLMCall.tenant_id == p.tenant_id)).scalar_one()
    return {"tokens_estimated": bool(estimated), "prompts": list_prompts(),
            "by_agent": [{"agent": a, "model": m, "prompt": f"{pn}@{pv}", "calls": c, "input_tokens": int(i or 0), "output_tokens": int(o or 0),
                          "cost_usd": round(float(cost or 0), 4), "avg_latency_ms": round(float(lat or 0), 1), "failures": int(f or 0)}
                         for a, m, pn, pv, c, i, o, cost, lat, f in rows]}
