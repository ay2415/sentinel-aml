"""Deterministic approval policy. Code, not the LLM, decides whether a human must approve.

Mirrors the tenant SOPs in the knowledge base (emerald-sop-alert-handling, liffey-sop-alert-handling).
Production: externalise to a policy-as-code engine (e.g. OPA/Rego) with versioned, audited rules.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.schemas import DecisionOutput, VerificationReport
from app.database.models import Alert, Customer, RecommendedAction, Role

POLICY_VERSION = "approval-policy-2026.05"
CASH_INTENSIVE_SHARE = 0.30  # cash deposits as a share of 30-day inflows
TENANT_POLICY = {
    "emerald": {"auto_close_allowed": True, "auto_close_max_score": 0.05, "auto_close_max_anomaly": 0.90, "auto_monitoring_allowed": True},
    "liffey": {"auto_close_allowed": False, "auto_close_max_score": 0.0, "auto_close_max_anomaly": 0.0, "auto_monitoring_allowed": True},
}
DEFAULT_POLICY = {"auto_close_allowed": False, "auto_close_max_score": 0.0, "auto_close_max_anomaly": 0.0, "auto_monitoring_allowed": False}


@dataclass
class PolicyDecision:
    requires_approval: bool
    required_role: Role | None
    reasons: list[str] = field(default_factory=list)
    policy_version: str = POLICY_VERSION


def evaluate(decision: DecisionOutput, alert: Alert, customer: Customer, verification: VerificationReport,
             injection_flags: list) -> PolicyDecision:
    p = TENANT_POLICY.get(alert.tenant_id, DEFAULT_POLICY)
    a = decision.action
    reasons: list[str] = []
    if a in (RecommendedAction.ESCALATE_STR, RecommendedAction.RESTRICT_ACCOUNT):
        return PolicyDecision(True, Role.mlro, [f"{a.value} always requires MLRO approval"])
    blockers = []
    if not verification.passed:
        blockers.append("verification_failed")
    if injection_flags:
        blockers.append("prompt_injection_indicators_in_case_data")
    if customer.kyc_risk_rating == "high" or customer.pep_flag:
        blockers.append("high_risk_or_pep_customer")
    if a == RecommendedAction.CLOSE_FALSE_POSITIVE:
        if not p["auto_close_allowed"]:
            blockers.append("tenant_policy_disallows_auto_close")
        if alert.risk_score is None or alert.risk_score >= p["auto_close_max_score"]:
            blockers.append("risk_score_above_auto_close_limit")
        if alert.anomaly_score is None or alert.anomaly_score >= p["auto_close_max_anomaly"]:
            blockers.append("anomaly_percentile_above_auto_close_limit")
        # Added 2026.05 after agent evaluation: every suspicious alert the model scored below the auto-close limit was an
        # SME cash business. Cash-intensive businesses carry higher inherent placement risk, so a human always closes them.
        if customer.segment == "sme" and float(alert.features.get("cash_in_share_30d", 0.0)) >= CASH_INTENSIVE_SHARE:
            blockers.append("cash_intensive_business_requires_human_closure")
    if a == RecommendedAction.ENHANCED_MONITORING and not p["auto_monitoring_allowed"]:
        blockers.append("tenant_policy_disallows_auto_monitoring")
    if blockers:
        return PolicyDecision(True, Role.investigator, reasons + blockers)
    return PolicyDecision(False, None, ["all auto-apply conditions satisfied"])
