"""Structured input/output contracts for every agent. The LLM can only produce these shapes."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.database.models import Priority, RecommendedAction

EVIDENCE_REF = r"^(TX|FEATURE|PROFILE|ALERT|CP):[A-Za-z0-9_\-\.]{1,80}$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Typology(str, Enum):
    STRUCTURING = "STRUCTURING"
    MONEY_MULE = "MONEY_MULE"
    HIGH_RISK_JURISDICTION = "HIGH_RISK_JURISDICTION"
    FUNNEL_ACCOUNT = "FUNNEL_ACCOUNT"
    DORMANT_REACTIVATION = "DORMANT_REACTIVATION"
    UNUSUAL_VOLUME = "UNUSUAL_VOLUME"
    NONE_APPARENT = "NONE_APPARENT"


# ------------------------------------------------------------------ triage
class TriageOutput(Strict):
    typology_hypotheses: list[Typology] = Field(min_length=1, max_length=3)
    priority: Priority
    investigation_depth: Literal["standard", "enhanced"]
    rationale: str = Field(max_length=600)


# ------------------------------------------------------------------ investigation
VerifiableMetric = Literal[
    "sum_amount_eur_of_refs", "count_of_refs", "total_in_30d", "total_out_30d", "total_in_7d", "total_out_7d",
    "near_threshold_cash_count_30d", "cash_in_amount_30d", "unique_counterparties_in_30d", "unique_cash_branches_30d", "high_risk_country_amount_30d",
    "crypto_out_amount_30d", "intl_wire_out_amount_30d", "pass_through_ratio_7d", "turnover_vs_expected_30d",
    "dormancy_gap_days", "account_age_days", "risk_score",
]


class QuantClaim(Strict):
    metric: VerifiableMetric
    value: float


class Finding(Strict):
    finding_id: str = Field(pattern=r"^F\d{1,2}$")
    statement: str = Field(min_length=10, max_length=400)
    direction: Literal["aggravating", "mitigating", "neutral"]
    evidence_refs: list[str] = Field(min_length=1, max_length=25)
    quantitative_claims: list[QuantClaim] = Field(default_factory=list, max_length=5)

    @field_validator("evidence_refs")
    @classmethod
    def _refs(cls, v: list[str]) -> list[str]:
        import re

        bad = [r for r in v if not re.match(EVIDENCE_REF, r)]
        if bad:
            raise ValueError(f"malformed evidence refs: {bad[:3]}")
        return v


ToolName = Literal["get_alert_features", "get_customer_profile", "get_transactions", "get_counterparty_summary", "get_prior_alerts"]


class ToolCallRequest(Strict):
    tool: ToolName
    arguments: dict = Field(default_factory=dict)
    reason: str = Field(default="", max_length=200)


class InvestigationStep(Strict):
    action: Literal["call_tool", "finish"]
    tool_call: ToolCallRequest | None = None
    findings: list[Finding] | None = Field(default=None, max_length=12)


# ------------------------------------------------------------------ policy
class Guidance(Strict):
    summary: str = Field(max_length=300)
    chunk_ids: list[str] = Field(min_length=1, max_length=4)
    relevance: Literal["supports_suspicion", "supports_legitimate", "procedural"]


class RedFlagMatch(Strict):
    indicator: str = Field(max_length=200)
    finding_ids: list[str] = Field(min_length=1)
    chunk_ids: list[str] = Field(min_length=1)


class PolicyOutput(Strict):
    applicable_guidance: list[Guidance] = Field(max_length=8)
    red_flags_matched: list[RedFlagMatch] = Field(default_factory=list, max_length=10)
    approval_requirements: list[Guidance] = Field(default_factory=list, max_length=4)
    reporting_consideration: Literal["not_indicated", "consider_escalation", "escalation_indicated"]


# ------------------------------------------------------------------ decision
class KeyFinding(Strict):
    statement: str = Field(min_length=10, max_length=400)
    evidence_refs: list[str] = Field(min_length=1, max_length=25)


class DecisionOutput(Strict):
    action: RecommendedAction
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = Field(min_length=10, max_length=600)
    narrative: str = Field(min_length=50, max_length=4000)
    key_findings: list[KeyFinding] = Field(min_length=1, max_length=8)
    citations: list[str] = Field(default_factory=list, max_length=10)
    mitigating_factors: list[str] = Field(default_factory=list, max_length=5)
    residual_uncertainty: str = Field(default="", max_length=400)


# ------------------------------------------------------------------ verification (deterministic)
class VerificationIssue(Strict):
    code: str
    severity: Literal["critical", "warning"]
    detail: str


class VerificationReport(Strict):
    passed: bool
    issues: list[VerificationIssue]
    groundedness: float
    citation_validity: float
    quantitative_claims_checked: int
    quantitative_claims_failed: int
