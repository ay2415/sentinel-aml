"""Knowledge-base ingestion with poisoning defences.

Pipeline: parse -> validate metadata -> hash -> injection scan -> chunk -> embed -> store.
Trust model:
* Repository-seeded documents are reviewed via pull request, so they are APPROVED on index.
* API uploads start PENDING_REVIEW and are invisible to retrieval until approved by a different
  user with kb:approve (four-eyes). Any injection finding QUARANTINES the document.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.logging import log_event
from app.database.models import DocStatus, KBChunk, KBDocument, Tenant
from app.rag.documents import chunk_document, parse_markdown
from app.rag.embeddings import get_embedder
from app.security import prompt_guard
from app.security.rbac import CLASSIFICATION_LEVELS

log = logging.getLogger(__name__)


def index_document(db: Session, raw: str, *, uploaded_by: str, trusted_source: bool,
                   forced_tenant: str | None = None) -> KBDocument:
    doc = parse_markdown(raw)
    meta = doc.meta
    tenant = forced_tenant if forced_tenant is not None else (meta.get("tenant_id") or None)
    if forced_tenant is not None and meta.get("tenant_id") not in (None, "", forced_tenant):
        raise ForbiddenError("document tenant_id does not match uploader tenant")  # tenant isolation on write
    if tenant and db.get(Tenant, tenant) is None:
        raise NotFoundError(f"unknown tenant {tenant}")
    existing = db.get(KBDocument, meta["doc_id"])
    if existing and existing.content_sha256 == doc.sha256:
        return existing  # idempotent re-index
    if existing and existing.tenant_id != tenant:
        raise ConflictError("doc_id already used by another tenant")

    findings = [f.__dict__ for f in prompt_guard.scan(doc.body, context="document")]
    if findings:
        status = DocStatus.QUARANTINED
    elif trusted_source:
        status = DocStatus.APPROVED
    else:
        status = DocStatus.PENDING_REVIEW
    level = CLASSIFICATION_LEVELS[meta["classification"]]
    if existing:
        db.delete(existing)
        db.flush()
    kb = KBDocument(id=meta["doc_id"], tenant_id=tenant, title=str(meta["title"])[:300], doc_type=meta["doc_type"],
                    source=str(meta["source"])[:300], classification=meta["classification"], clearance_level=level,
                    version=str(meta.get("version", "1.0")), content_sha256=doc.sha256, status=status,
                    injection_findings=findings, uploaded_by=uploaded_by,
                    approved_by="repository-review" if status == DocStatus.APPROVED else None)
    db.add(kb)
    chunks = chunk_document(doc)
    emb = get_embedder()
    vectors = emb.embed([c.text for c in chunks]) if chunks else []
    for c, v in zip(chunks, vectors, strict=True):
        db.add(KBChunk(id=f"{kb.id}#{c.index:03d}", document_id=kb.id, tenant_id=tenant, clearance_level=level,
                       is_active=status == DocStatus.APPROVED, chunk_index=c.index, section=c.section[:300], text=c.text,
                       token_estimate=int(c.words * 1.35), embedding=v.tolist(), embedding_model=emb.name))
    db.flush()
    log_event(log, "kb_document_indexed", doc_id=kb.id, status=status.value, chunks=len(chunks), injection_findings=len(findings))
    return kb


def approve_document(db: Session, doc_id: str, approver: str, approver_tenant: str) -> KBDocument:
    kb = db.get(KBDocument, doc_id)
    if kb is None or (kb.tenant_id not in (None, approver_tenant)):
        raise NotFoundError("document not found")
    if kb.tenant_id is None:
        raise ForbiddenError("global documents are managed through repository review only")
    if kb.status == DocStatus.QUARANTINED:
        raise ConflictError("quarantined documents cannot be approved; upload a clean revision")
    if kb.uploaded_by == approver:
        raise ForbiddenError("four-eyes: uploader cannot approve their own document")
    kb.status, kb.approved_by = DocStatus.APPROVED, approver
    for ch in db.execute(select(KBChunk).where(KBChunk.document_id == doc_id)).scalars():
        ch.is_active = True
    db.flush()
    return kb


def index_directory(db: Session, directory: Path) -> dict:
    out = {"indexed": 0, "quarantined": 0, "chunks": 0}
    for path in sorted(directory.glob("*.md")):
        kb = index_document(db, path.read_text(encoding="utf-8"), uploaded_by="repository", trusted_source=True)
        out["indexed"] += 1
        out["quarantined"] += int(kb.status == DocStatus.QUARANTINED)
    out["chunks"] = db.query(KBChunk).count()
    return out
