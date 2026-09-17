"""SentinelAML API application factory."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app.api.middleware import RequestContextMiddleware
from app.api.routes import alerts, analytics, audit, auth, health, ingestion, investigations, knowledge, ops, recommendations
from app.core.config import get_settings
from app.core.context import request_id_var
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.observability.tracing import setup_tracing

log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging(get_settings().log_level)
    setup_tracing()
    log.info("startup complete")
    yield


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="SentinelAML API", version="1.0.0", lifespan=lifespan,
                  description="Agentic AML alert investigation platform: ML risk scoring, access-controlled RAG, "
                              "verified multi-agent recommendations and human approval.")
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_methods=["GET", "POST"],
                       allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"])

    def err(status: int, code: str, message: str, details=None):
        return JSONResponse({"error": {"code": code, "message": message, "details": details or {}, "request_id": request_id_var.get()}}, status)

    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError):
        return err(exc.status_code, exc.code, exc.message, {k: v for k, v in exc.details.items() if k != "llm_call_id"})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        return err(422, "validation_failed", "request validation failed",
                   {"errors": [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()][:10]})

    from sqlalchemy.orm.exc import StaleDataError

    @app.exception_handler(StaleDataError)
    async def stale(_: Request, exc: StaleDataError):
        return err(409, "concurrent_modification", "the resource was modified concurrently; retry")

    @app.exception_handler(OperationalError)
    async def db_down(_: Request, exc: OperationalError):
        log.error("database unavailable")
        return err(503, "database_unavailable", "database temporarily unavailable")

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.error("unhandled error: %s", type(exc).__name__)
        return err(500, "internal_error", "internal server error")  # never leak stack traces

    app.include_router(health.router)
    for r in (auth, alerts, investigations, recommendations, knowledge, ingestion, analytics, ops, audit):
        app.include_router(r.router, prefix="/api/v1")
    return app


app = create_app()
