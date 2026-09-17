from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.schemas import DocumentUpload, SearchRequest
from app.core.errors import ValidationFailedError
from app.database.models import KBDocument
from app.rag.documents import DocumentFormatError
from app.rag.indexer import approve_document, index_document
from app.rag.retriever import AccessContext, Retriever
from app.security.auth import Principal, require
from app.security.rbac import Permission, clearance_for
from app.services import audit

router = APIRouter(prefix="/kb", tags=["knowledge-base"])
_retriever = Retriever()


@router.post("/search", summary="ACL-filtered hybrid retrieval (tenant + clearance enforced in the query)")
def search(body: SearchRequest, p: Principal = Depends(require(Permission.SEARCH_KB)), db: Session = Depends(get_db)):
    res, meta = _retriever.search(db, body.query, AccessContext(p.tenant_id, clearance_for(p.role)), top_k=body.top_k, mode=body.mode)
    return {"meta": meta, "results": [{"chunk_id": r.chunk_id, "document_id": r.document_id, "title": r.title, "section": r.section,
                                       "score": r.score, "dense_rank": r.dense_rank, "lexical_rank": r.lexical_rank, "text": r.text} for r in res]}


@router.get("/documents")
def documents(p: Principal = Depends(require(Permission.SEARCH_KB)), db: Session = Depends(get_db)):
    lvl = clearance_for(p.role)
    rows = db.execute(select(KBDocument).where((KBDocument.tenant_id.is_(None)) | (KBDocument.tenant_id == p.tenant_id))
                      .where(KBDocument.clearance_level <= lvl)).scalars().all()
    return [{"id": d.id, "title": d.title, "doc_type": d.doc_type, "classification": d.classification, "status": d.status.value,
             "version": d.version, "tenant_id": d.tenant_id, "injection_findings": d.injection_findings} for d in rows]


@router.post("/documents", status_code=201, summary="Upload a tenant document (starts PENDING_REVIEW or QUARANTINED)")
def upload(body: DocumentUpload, p: Principal = Depends(require(Permission.UPLOAD_KB)), db: Session = Depends(get_db)):
    try:
        kb = index_document(db, body.content, uploaded_by=p.actor, trusted_source=False, forced_tenant=p.tenant_id)
    except DocumentFormatError as exc:
        raise ValidationFailedError(str(exc)) from exc
    audit.record(db, tenant_id=p.tenant_id, actor=p.actor, action="kb_document_uploaded", entity_type="kb_document", entity_id=kb.id,
                 details={"status": kb.status.value, "findings": len(kb.injection_findings)})
    db.commit()
    return {"id": kb.id, "status": kb.status.value, "injection_findings": kb.injection_findings}


@router.post("/documents/{doc_id}/approve")
def approve(doc_id: str, p: Principal = Depends(require(Permission.APPROVE_KB)), db: Session = Depends(get_db)):
    kb = approve_document(db, doc_id, approver=p.actor, approver_tenant=p.tenant_id)
    audit.record(db, tenant_id=p.tenant_id, actor=p.actor, action="kb_document_approved", entity_type="kb_document", entity_id=kb.id)
    db.commit()
    return {"id": kb.id, "status": kb.status.value}
