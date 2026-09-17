"""Human-in-the-loop decisions: authority matrix, four-eyes, audit, then allow-listed execution."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationFailedError
from app.database.models import Alert, AlertStatus, Approval, Recommendation, RecommendationStatus, WorkflowRun, WorkflowStatus
from app.security.auth import Principal
from app.security.rbac import APPROVAL_AUTHORITY
from app.services import actions, audit


def decide(db: Session, rec_id: str, principal: Principal, decision: str, comment: str) -> dict:
    rec = db.execute(select(Recommendation).where(Recommendation.id == rec_id, Recommendation.tenant_id == principal.tenant_id)
                     .with_for_update()).scalar_one_or_none()
    if rec is None:
        raise NotFoundError("recommendation not found")
    if rec.status != RecommendationStatus.PENDING_APPROVAL:
        raise ConflictError(f"recommendation already {rec.status.value}")
    if not rec.verification.get("passed", False) and decision == "approve":
        raise ConflictError("recommendation failed verification; it can only be rejected and investigated manually")
    if principal.role not in APPROVAL_AUTHORITY[rec.action]:
        raise ForbiddenError(f"role {principal.role.value} cannot approve {rec.action.value}")
    run = db.get(WorkflowRun, rec.workflow_id)
    if run.started_by == principal.actor:
        raise ForbiddenError("four-eyes principle: the initiator of an investigation cannot approve its recommendation")
    if decision not in ("approve", "reject"):
        raise ValidationFailedError("decision must be approve or reject")
    if len(comment.strip()) < 10:
        raise ValidationFailedError("a rationale of at least 10 characters is required")
    db.add(Approval(tenant_id=principal.tenant_id, recommendation_id=rec.id, decided_by=principal.actor,
                    decided_by_role=principal.role.value, decision=decision, comment=comment.strip()))
    audit.record(db, tenant_id=principal.tenant_id, actor=principal.actor, action=f"recommendation_{decision}d", entity_type="recommendation",
                 entity_id=rec.id, details={"action": rec.action.value, "comment": comment.strip()[:500]})
    alert = db.get(Alert, rec.alert_id)
    if decision == "reject":
        rec.status = RecommendationStatus.REJECTED
        alert.status = AlertStatus.NEEDS_MANUAL_REVIEW
        run.status = WorkflowStatus.NEEDS_MANUAL_REVIEW
        db.flush()
        return {"status": rec.status.value, "alert_status": alert.status.value}
    rec.status = RecommendationStatus.APPROVED
    db.flush()
    effects = actions.execute(db, rec, actor=principal.actor)
    run.status = WorkflowStatus.COMPLETED
    db.flush()
    return {"status": rec.status.value, "alert_status": alert.status.value, "effects": effects}
