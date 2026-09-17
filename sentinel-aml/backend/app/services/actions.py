"""Allow-listed action execution. The only code path that changes case outcomes.
Invoked by the orchestrator (auto-apply allowed by policy) or by the approval service (human approved)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.database.models import Account, Alert, AlertStatus, Customer, Recommendation, RecommendationStatus, RecommendedAction, STRReport
from app.services import audit


def execute(db: Session, rec: Recommendation, actor: str) -> dict:
    if rec.status not in (RecommendationStatus.APPROVED, RecommendationStatus.AUTO_APPLIED):
        raise ConflictError(f"recommendation in status {rec.status.value} cannot be executed")
    alert = db.execute(select(Alert).where(Alert.id == rec.alert_id, Alert.tenant_id == rec.tenant_id)).scalar_one()
    effects: dict = {"action": rec.action.value}
    if rec.action == RecommendedAction.CLOSE_FALSE_POSITIVE:
        alert.status = AlertStatus.CLOSED_FALSE_POSITIVE
    elif rec.action == RecommendedAction.ENHANCED_MONITORING:
        alert.status = AlertStatus.MONITORING
        cust = db.get(Customer, alert.customer_id)
        cust.monitoring_level = "enhanced"
        effects["monitoring_level"] = "enhanced"
    elif rec.action in (RecommendedAction.ESCALATE_STR, RecommendedAction.RESTRICT_ACCOUNT):
        db.add(STRReport(tenant_id=rec.tenant_id, alert_id=alert.id, recommendation_id=rec.id, narrative=rec.narrative, approved_by=actor))
        effects["str_report"] = "READY_FOR_SUBMISSION"  # production: goAML XML export + MLRO submission step
        alert.status = AlertStatus.ESCALATED_STR
        if rec.action == RecommendedAction.RESTRICT_ACCOUNT:
            accounts = db.execute(select(Account).where(Account.customer_id == alert.customer_id, Account.tenant_id == rec.tenant_id)).scalars()
            restricted = []
            for acc in accounts:
                acc.status = "restricted"
                restricted.append(acc.id)
            alert.status = AlertStatus.ACCOUNT_RESTRICTED
            effects["restricted_accounts"] = restricted
    if rec.status == RecommendationStatus.APPROVED:
        rec.status = RecommendationStatus.EXECUTED
    db.flush()
    audit.record(db, tenant_id=rec.tenant_id, actor=actor, action="action_executed", entity_type="alert", entity_id=alert.id, details=effects)
    return effects
