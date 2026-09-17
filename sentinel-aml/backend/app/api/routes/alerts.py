from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, paginate
from app.api.schemas import AlertDetail, AlertOut, Page, StartInvestigation, WorkflowOut, from_orm
from app.core.errors import NotFoundError
from app.database.models import Alert, AlertStatus, Customer, ModelVersion, Prediction, Priority, WorkflowRun, WorkflowStatus
from app.security.auth import Principal, require
from app.security.rbac import Permission, has_permission

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=Page[AlertOut])
def list_alerts(status: AlertStatus | None = None, priority: Priority | None = None, rule_code: str | None = Query(None, max_length=40),
                min_score: float | None = Query(None, ge=0, le=1), page: int = 1, page_size: int = 25,
                sort: str = Query("risk_desc", pattern="^(risk_desc|newest)$"),
                p: Principal = Depends(require(Permission.READ_ALERTS)), db: Session = Depends(get_db)):
    q = select(Alert).where(Alert.tenant_id == p.tenant_id)
    if status:
        q = q.where(Alert.status == status)
    if priority:
        q = q.where(Alert.priority == priority)
    if rule_code:
        q = q.where(Alert.rule_code == rule_code)
    if min_score is not None:
        q = q.where(Alert.risk_score >= min_score)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    order = (Alert.risk_score.desc().nulls_last(), Alert.id) if sort == "risk_desc" else (Alert.triggered_at.desc(), Alert.id)
    off, size = paginate(page, page_size)
    items = db.execute(q.order_by(*order).offset(off).limit(size)).scalars().all()
    return Page(items=[from_orm(AlertOut, a) for a in items],
                total=total, page=page, page_size=size)


@router.get("/{alert_id}", response_model=AlertDetail)
def get_alert(alert_id: str, p: Principal = Depends(require(Permission.READ_ALERTS)), db: Session = Depends(get_db)):
    a = db.execute(select(Alert).where(Alert.id == alert_id, Alert.tenant_id == p.tenant_id)).scalar_one_or_none()
    if a is None:
        raise NotFoundError("alert not found")
    c = db.get(Customer, a.customer_id)
    pii = has_permission(p.role, Permission.VIEW_PII)
    customer = {"id": c.id, "segment": c.segment, "occupation": c.occupation, "kyc_risk_rating": c.kyc_risk_rating, "pep_flag": c.pep_flag,
                "expected_monthly_turnover_eur": c.expected_monthly_turnover_eur, "monitoring_level": c.monitoring_level,
                "full_name": c.full_name if pii else "[restricted]"}
    pred = db.execute(select(Prediction, ModelVersion).join(ModelVersion, ModelVersion.id == Prediction.model_version_id)
                      .where(Prediction.alert_id == a.id).order_by(Prediction.created_at.desc())).first()
    prediction = None
    if pred:
        pr, mv = pred
        prediction = {"prediction_id": pr.id, "score": pr.score, "raw_score": pr.raw_score, "anomaly_percentile": pr.anomaly_score,
                      "above_threshold": pr.above_threshold, "top_contributors": pr.top_contributors, "created_at": pr.created_at,
                      "model": {"name": mv.name, "version": mv.version, "algorithm": mv.algorithm, "artifact_sha256": mv.artifact_sha256,
                                "training_data_hash": mv.training_data_hash}}
    runs = db.execute(select(WorkflowRun).where(WorkflowRun.alert_id == a.id).order_by(WorkflowRun.created_at.desc())).scalars().all()
    return AlertDetail(**{**from_orm(AlertOut, a).model_dump(),
                          "rule_details": a.rule_details, "features": a.features, "feature_set_version": a.feature_set_version,
                          "customer": customer, "prediction": prediction,
                          "workflows": [{"id": r.id, "status": r.status.value, "created_at": r.created_at} for r in runs]})


@router.post("/{alert_id}/investigations", response_model=WorkflowOut, status_code=201,
             summary="Start the agentic investigation (Idempotency-Key header supported)")
def start_investigation(alert_id: str, background: BackgroundTasks, body: StartInvestigation | None = None,
                        idempotency_key: str | None = Header(None, max_length=100),
                        p: Principal = Depends(require(Permission.START_INVESTIGATION)), db: Session = Depends(get_db)):
    from app.agents.orchestrator import Orchestrator

    body = body or StartInvestigation()
    orch = Orchestrator()
    if body.mode == "async":  # production: enqueue to Azure Service Bus; worker picks up QUEUED runs
        run, _ = orch.create_run(db, alert_id, p, idempotency_key, status=WorkflowStatus.QUEUED)
        db.commit()
        return JSONResponse(from_orm(WorkflowOut, run).model_dump(mode="json"), status_code=202)
    run = orch.run_sync(db, alert_id, p, idempotency_key)
    db.commit()
    return from_orm(WorkflowOut, run)
