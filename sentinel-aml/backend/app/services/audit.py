"""Hash-chained audit log. Each record's hash covers its content plus the previous hash,
so deleting or editing any historical row breaks verification.

Production: additionally stream to immutable storage (Azure Blob with time-based retention /
WORM policy) because a DB superuser could rewrite the whole chain.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.context import request_id_var
from app.database.models import AuditLog, utcnow

GENESIS = "0" * 64


def _digest(prev_hash: str, tenant_id: str, actor: str, action: str, entity_type: str, entity_id: str,
            details: dict, created_at_iso: str) -> str:
    body = json.dumps([prev_hash, tenant_id, actor, action, entity_type, entity_id, details, created_at_iso],
                      sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def _append(db: Session, *, tenant_id, actor, action, entity_type, entity_id, details) -> AuditLog:
    last = db.execute(select(AuditLog).where(AuditLog.tenant_id == tenant_id).order_by(AuditLog.id.desc()).limit(1)).scalar_one_or_none()
    prev = last.hash if last else GENESIS
    created = utcnow().replace(microsecond=0)
    row = AuditLog(tenant_id=tenant_id, actor=actor, action=action, entity_type=entity_type, entity_id=entity_id,
                   details=details, request_id=request_id_var.get(), prev_hash=prev, created_at=created,
                   hash=_digest(prev, tenant_id, actor, action, entity_type, entity_id, details, created.isoformat()))
    db.add(row)
    db.flush()
    return row


def record(db: Session, *, tenant_id: str, actor: str, action: str, entity_type: str, entity_id: str,
           details: dict | None = None) -> AuditLog:
    """Append one audit event to the tenant's hash chain.

    PostgreSQL: an autonomous short transaction serialised by a per-tenant advisory lock. Rationale: `SELECT ... FOR UPDATE`
    on the latest row does NOT prevent two concurrent writers from chaining onto the same predecessor (found by the load
    test), and holding a lock inside the business transaction would serialise investigations for the duration of LLM calls.
    Consequence (accepted, documented): an event is kept even if the surrounding business transaction later rolls back,
    so the trail records attempts as well as outcomes. SQLite (tests) is single-writer, so it appends in-transaction.
    """
    details = json.loads(json.dumps(details or {}, default=str))
    kwargs = dict(tenant_id=tenant_id, actor=actor, action=action, entity_type=entity_type, entity_id=entity_id, details=details)
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return _append(db, **kwargs)
    engine = getattr(bind, "engine", bind)
    with Session(bind=engine, expire_on_commit=False) as s:
        s.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"audit-chain:{tenant_id}"})
        row = _append(s, **kwargs)
        s.commit()
        return row


def verify_chain(db: Session, tenant_id: str) -> dict:
    prev = GENESIS
    rows = db.execute(select(AuditLog).where(AuditLog.tenant_id == tenant_id).order_by(AuditLog.id)).scalars()
    n = 0
    for r in rows:
        created = r.created_at.replace(tzinfo=None).isoformat() if r.created_at.tzinfo is None else r.created_at.isoformat()
        expected = _digest(prev, r.tenant_id, r.actor, r.action, r.entity_type, r.entity_id, r.details, created)
        if r.prev_hash != prev or r.hash != expected:
            # SQLite drops tzinfo; retry with +00:00 form before declaring tamper
            alt = _digest(prev, r.tenant_id, r.actor, r.action, r.entity_type, r.entity_id, r.details,
                          r.created_at.isoformat() + "+00:00")
            if not (r.prev_hash == prev and r.hash == alt):
                return {"valid": False, "records_checked": n, "broken_at_id": r.id}
        prev = r.hash
        n += 1
    return {"valid": True, "records_checked": n}
