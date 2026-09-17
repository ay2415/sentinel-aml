"""Request-scoped context propagated to logs, metrics and audit records."""
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
tenant_id_var: ContextVar[str] = ContextVar("tenant_id", default="-")
user_var: ContextVar[str] = ContextVar("user", default="-")
workflow_id_var: ContextVar[str] = ContextVar("workflow_id", default="-")
