from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, paginate
from app.database.models import AuditLog
from app.security.auth import Principal, require
from app.security.rbac import Permission
from app.services.audit import verify_chain

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
def list_audit(page: int = 1, page_size: int = 50, action: str | None = None, entity_id: str | None = None,
               p: Principal = Depends(require(Permission.READ_AUDIT)), db: Session = Depends(get_db)):
    q = select(AuditLog).where(AuditLog.tenant_id == p.tenant_id)
    if action:
        q = q.where(AuditLog.action == action)
    if entity_id:
        q = q.where(AuditLog.entity_id == entity_id)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    off, size = paginate(page, page_size)
    rows = db.execute(q.order_by(AuditLog.id.desc()).offset(off).limit(size)).scalars().all()
    return {"total": total, "page": page, "page_size": size,
            "items": [{"id": r.id, "at": r.created_at, "actor": r.actor, "action": r.action, "entity_type": r.entity_type, "entity_id": r.entity_id,
                       "details": r.details, "request_id": r.request_id, "hash": r.hash[:16]} for r in rows]}


@router.get("/verify", summary="Verify the tamper-evident hash chain for this tenant")
def verify(p: Principal = Depends(require(Permission.READ_AUDIT)), db: Session = Depends(get_db)):
    return verify_chain(db, p.tenant_id)
