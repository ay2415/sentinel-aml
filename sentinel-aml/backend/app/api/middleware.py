"""Cross-cutting HTTP concerns: correlation ids, metrics, rate limiting, security headers, body limits."""
from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.core.context import request_id_var
from app.observability import metrics as m
from app.security.rate_limit import InMemoryRateLimiter

limiter = InMemoryRateLimiter(get_settings().rate_limit_per_minute)
login_limiter = InMemoryRateLimiter(get_settings().login_rate_limit_per_minute)  # brute-force protection on the token endpoint


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        if len(rid) > 64 or not all(c.isalnum() or c in "-_" for c in rid):
            rid = str(uuid.uuid4())  # never trust/echo malformed client ids into logs
        request_id_var.set(rid)
        s = get_settings()
        if int(request.headers.get("content-length") or 0) > s.max_upload_bytes:
            return JSONResponse({"error": {"code": "payload_too_large", "message": "request body too large"}}, 413)
        client = request.client.host if request.client else "unknown"
        auth = request.headers.get("authorization", "")
        key = f"tok:{auth[-24:]}" if auth else f"ip:{client}"
        lim = login_limiter if request.url.path.endswith("/auth/token") else limiter
        allowed, remaining = lim.allow(f"{request.url.path.endswith('/auth/token')}:{key}")
        if not allowed:
            m.HTTP_REQUESTS.labels(request.method, "rate_limited", "429").inc()
            return JSONResponse({"error": {"code": "rate_limited", "message": "too many requests"}}, 429,
                                headers={"Retry-After": "60", "x-request-id": rid})
        t0 = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        m.HTTP_REQUESTS.labels(request.method, path, str(response.status_code)).inc()
        m.HTTP_LATENCY.labels(request.method, path).observe(time.perf_counter() - t0)
        response.headers["x-request-id"] = rid
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if not request.url.path.startswith(("/docs", "/redoc", "/openapi")):
            response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response
