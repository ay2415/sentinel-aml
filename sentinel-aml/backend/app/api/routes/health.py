from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import get_settings
from app.ml.scoring import model_service

router = APIRouter(tags=["health"])


@router.get("/health/live")
def live():
    return {"status": "alive"}


@router.get("/health/ready")
def ready(db: Session = Depends(get_db)):
    checks = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:  # noqa: BLE001
        checks["database"] = "unavailable"
    checks["model"] = "ok" if checks["database"] == "ok" and model_service.is_ready(db) else "unavailable"
    try:
        n = db.execute(text("SELECT count(*) FROM kb_chunks WHERE is_active")).scalar_one() if checks["database"] == "ok" else 0
        checks["knowledge_base"] = "ok" if n > 0 else "empty"
    except Exception:  # noqa: BLE001
        checks["knowledge_base"] = "unavailable"
    checks["llm_provider"] = get_settings().llm_provider
    ok = checks["database"] == "ok" and checks["model"] == "ok"
    return JSONResponse({"status": "ready" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503)


@router.get("/metrics", include_in_schema=False)
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
