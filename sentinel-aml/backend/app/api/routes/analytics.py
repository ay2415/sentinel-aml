"""Business KPIs for compliance leadership (the analytics layer; also exportable to Power BI)."""
from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.database.models import Alert, AlertStatus, Recommendation, RecommendationStatus, WorkflowRun
from app.security.auth import Principal, require
from app.security.rbac import Permission

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/kpis")
def kpis(p: Principal = Depends(require(Permission.READ_ALERTS)), db: Session = Depends(get_db)):
    t = p.tenant_id
    by_status = dict(db.execute(select(Alert.status, func.count()).where(Alert.tenant_id == t).group_by(Alert.status)).all())
    by_priority = dict(db.execute(select(Alert.priority, func.count()).where(Alert.tenant_id == t).group_by(Alert.priority)).all())
    by_rule = db.execute(select(Alert.rule_code, func.count(), func.avg(Alert.risk_score),
                                func.sum(case((Alert.risk_score >= 0.5, 1), else_=0))).where(Alert.tenant_id == t).group_by(Alert.rule_code)).all()
    wf = db.execute(select(func.count(), func.avg(WorkflowRun.latency_ms), func.sum(WorkflowRun.total_tokens), func.sum(WorkflowRun.total_cost_usd))
                    .where(WorkflowRun.tenant_id == t)).one()
    recs = dict(db.execute(select(Recommendation.action, func.count()).where(Recommendation.tenant_id == t).group_by(Recommendation.action)).all())
    pending = db.execute(select(func.count()).where(Recommendation.tenant_id == t, Recommendation.status == RecommendationStatus.PENDING_APPROVAL)).scalar_one()
    total = sum(by_status.values())
    return {
        "alerts_total": total, "alerts_by_status": {k.value: v for k, v in by_status.items()},
        "alerts_by_priority": {k.value: v for k, v in by_priority.items()},
        "open_alerts": sum(v for k, v in by_status.items() if k in (AlertStatus.NEW, AlertStatus.INVESTIGATING, AlertStatus.PENDING_APPROVAL,
                                                                    AlertStatus.NEEDS_MANUAL_REVIEW)),
        "rules": [{"rule_code": r, "alerts": n, "avg_risk_score": round(float(a or 0), 3), "high_risk_alerts": int(h or 0)} for r, n, a, h in by_rule],
        "investigations": {"count": wf[0], "avg_latency_ms": round(float(wf[1] or 0), 1), "total_tokens": int(wf[2] or 0),
                           "total_cost_usd": round(float(wf[3] or 0), 4)},
        "recommendations_by_action": {k.value: v for k, v in recs.items()}, "pending_approvals": pending,
    }


@router.get("/trends")
def trends(p: Principal = Depends(require(Permission.READ_ALERTS)), db: Session = Depends(get_db)):
    rows = db.execute(select(func.date(Alert.triggered_at), Alert.rule_code, func.count(), func.avg(Alert.risk_score))
                      .where(Alert.tenant_id == p.tenant_id).group_by(func.date(Alert.triggered_at), Alert.rule_code)
                      .order_by(func.date(Alert.triggered_at))).all()
    return [{"date": str(d), "rule_code": r, "alerts": n, "avg_risk_score": round(float(s or 0), 3)} for d, r, n, s in rows]
