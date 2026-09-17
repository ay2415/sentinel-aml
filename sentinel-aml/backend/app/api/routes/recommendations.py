from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, paginate
from app.api.schemas import DecisionRequest, Page, RecommendationOut, from_orm
from app.core.errors import NotFoundError
from app.database.models import Recommendation, RecommendationStatus
from app.security.auth import Principal, require
from app.security.rbac import Permission
from app.services import approvals

router = APIRouter(prefix="/recommendations", tags=["human-in-the-loop"])


def _out(r: Recommendation) -> RecommendationOut:
    return from_orm(RecommendationOut, r)


@router.get("", response_model=Page[RecommendationOut])
def list_recs(status: RecommendationStatus | None = None, page: int = 1, page_size: int = 25,
              p: Principal = Depends(require(Permission.READ_INVESTIGATIONS)), db: Session = Depends(get_db)):
    q = select(Recommendation).where(Recommendation.tenant_id == p.tenant_id)
    if status:
        q = q.where(Recommendation.status == status)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    off, size = paginate(page, page_size)
    rows = db.execute(q.order_by(Recommendation.created_at.desc()).offset(off).limit(size)).scalars().all()
    return Page(items=[_out(r) for r in rows], total=total, page=page, page_size=size)


@router.get("/{rec_id}", response_model=RecommendationOut)
def get_rec(rec_id: str, p: Principal = Depends(require(Permission.READ_INVESTIGATIONS)), db: Session = Depends(get_db)):
    r = db.execute(select(Recommendation).where(Recommendation.id == rec_id, Recommendation.tenant_id == p.tenant_id)).scalar_one_or_none()
    if r is None:
        raise NotFoundError("recommendation not found")
    return _out(r)


@router.post("/{rec_id}/decision", summary="Approve or reject a recommendation (authority matrix + four-eyes enforced)")
def decide(rec_id: str, body: DecisionRequest, p: Principal = Depends(require(Permission.DECIDE_RECOMMENDATION)),
           db: Session = Depends(get_db)):
    result = approvals.decide(db, rec_id, p, body.decision, body.comment)
    db.commit()
    return result
