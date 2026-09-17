import hashlib
import io

import pandas as pd
from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import get_settings
from app.core.errors import ValidationFailedError
from app.data.ingestion import ingest_transactions_frame
from app.database.models import IngestionBatch
from app.security.auth import Principal, require
from app.security.rbac import Permission
from app.services import audit

router = APIRouter(prefix="/ingestion", tags=["data-engineering"])


@router.post("/transactions", status_code=201, summary="Validate and idempotently load a transaction CSV batch")
async def upload(file: UploadFile = File(...), p: Principal = Depends(require(Permission.INGEST_DATA)), db: Session = Depends(get_db)):
    raw = await file.read(get_settings().max_upload_bytes + 1)
    if len(raw) > get_settings().max_upload_bytes:
        raise ValidationFailedError("file too large")
    try:
        df = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False, na_values=[""])
    except Exception as exc:  # noqa: BLE001
        raise ValidationFailedError(f"unreadable CSV: {type(exc).__name__}") from exc
    if "tenant_id" in df and (df["tenant_id"] != p.tenant_id).any():
        raise ValidationFailedError("batch contains rows for another tenant")
    batch = ingest_transactions_frame(db, df, source=(file.filename or "upload.csv")[:200], file_sha=hashlib.sha256(raw).hexdigest(), actor=p.actor)
    audit.record(db, tenant_id=p.tenant_id, actor=p.actor, action="ingestion_batch", entity_type="ingestion_batch", entity_id=batch.id,
                 details={"status": batch.status, "loaded": batch.rows_loaded, "rejected": batch.rows_rejected})
    db.commit()
    return {"batch_id": batch.id, "status": batch.status, "rows_received": batch.rows_received, "rows_loaded": batch.rows_loaded,
            "rows_rejected": batch.rows_rejected, "rows_duplicate": batch.rows_duplicate, "quality_report": batch.quality_report}


@router.get("/batches")
def batches(p: Principal = Depends(require(Permission.INGEST_DATA)), db: Session = Depends(get_db)):
    rows = db.execute(select(IngestionBatch).order_by(IngestionBatch.started_at.desc()).limit(50)).scalars().all()
    return [{"id": b.id, "entity": b.entity, "status": b.status, "file_sha256": b.file_sha256[:16], "loaded": b.rows_loaded,
             "rejected": b.rows_rejected, "duplicates": b.rows_duplicate, "started_at": b.started_at} for b in rows]
