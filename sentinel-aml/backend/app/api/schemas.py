"""Pydantic API contracts (request validation + OpenAPI documentation)."""
from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9_-]+$")
    username: str = Field(min_length=2, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth2 token type, not a secret
    expires_in: int
    role: str
    tenant_id: str


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    customer_id: str
    rule_code: str
    triggered_at: datetime
    status: str
    priority: str
    risk_score: float | None
    anomaly_score: float | None
    model_version_id: str | None


class AlertDetail(AlertOut):
    rule_details: dict
    features: dict
    feature_set_version: str
    customer: dict
    prediction: dict | None
    workflows: list[dict]


class StartInvestigation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["sync", "async"] = "sync"


class WorkflowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    alert_id: str
    status: str
    started_by: str
    llm_provider: str
    failure_reason: str | None
    total_tokens: int
    total_cost_usd: float
    latency_ms: float | None
    created_at: datetime
    finished_at: datetime | None


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    comment: str = Field(min_length=10, max_length=2000)


class RecommendationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workflow_id: str
    alert_id: str
    action: str
    confidence: float
    narrative: str
    key_findings: list
    citations: list
    verification: dict
    requires_approval: bool
    required_role: str | None
    policy_reasons: list
    status: str
    created_at: datetime


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=3, max_length=500)
    top_k: int = Field(default=6, ge=1, le=20)
    mode: Literal["dense", "lexical", "hybrid", "hybrid_rerank"] = "hybrid_rerank"


class DocumentUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=50, max_length=200_000, description="Markdown with YAML front matter")


def from_orm(model: type[BaseModel], obj, **overrides):
    """Build a response model from ORM attributes explicitly (never via __dict__, which omits unloaded attrs)."""
    data = {}
    for name in model.model_fields:
        if name in overrides:
            continue
        v = getattr(obj, name, None)
        data[name] = v.value if hasattr(v, "value") and not isinstance(v, (int, float)) else v
    data.update(overrides)
    return model.model_validate(data)
