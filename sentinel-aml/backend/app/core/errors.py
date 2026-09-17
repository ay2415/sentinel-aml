"""Domain exceptions mapped to consistent API error responses."""
from __future__ import annotations


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class ValidationFailedError(AppError):
    status_code = 422
    code = "validation_failed"


class DataQualityError(AppError):
    status_code = 422
    code = "data_quality_failed"


class LLMError(AppError):
    status_code = 502
    code = "llm_error"


class LLMTimeoutError(LLMError):
    code = "llm_timeout"


class CircuitOpenError(LLMError):
    status_code = 503
    code = "llm_circuit_open"


class ToolPermissionError(AppError):
    status_code = 403
    code = "tool_not_permitted"


class AgentLoopError(AppError):
    status_code = 500
    code = "agent_loop_detected"
