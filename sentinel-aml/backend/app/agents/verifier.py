"""Verification agent (deterministic). The LLM is untrusted, so every recommendation is checked
against system-of-record data before policy evaluation. No LLM is used here on purpose:
verification must be reproducible, cheap and immune to the same failure modes as the generator."""
from __future__ import annotations

from app.agents.schemas import DecisionOutput, Finding, PolicyOutput, VerificationIssue, VerificationReport
from app.agents.tools import CaseScope
from app.security.pii import contains_pii

FEATURE_METRICS = {"total_in_30d", "total_out_30d", "total_in_7d", "total_out_7d", "near_threshold_cash_count_30d", "cash_in_amount_30d",
                   "unique_counterparties_in_30d", "unique_cash_branches_30d", "high_risk_country_amount_30d", "crypto_out_amount_30d", "intl_wire_out_amount_30d",
                   "pass_through_ratio_7d", "turnover_vs_expected_30d", "dormancy_gap_days", "account_age_days"}


def _claim_ok(metric: str, value: float, refs: list[str], scope: CaseScope) -> bool:
    if metric == "sum_amount_eur_of_refs":
        amounts = [scope.tx_amounts[r] for r in refs if r in scope.tx_amounts]
        return bool(amounts) and abs(sum(amounts) - value) <= max(1.0, 0.01 * abs(value))
    if metric == "count_of_refs":
        return int(value) == len([r for r in refs if r.startswith("TX:")])
    if metric == "risk_score":
        return scope.alert.risk_score is not None and abs(scope.alert.risk_score - value) <= 0.01
    if metric in FEATURE_METRICS:
        actual = float(scope.alert.features.get(metric, float("nan")))
        return abs(actual - value) <= max(0.01, 0.01 * abs(actual))
    return False


def verify_findings(findings: list[Finding], scope: CaseScope) -> tuple[list[VerificationIssue], int, int]:
    issues, checked, failed = [], 0, 0
    for f in findings:
        unknown = [r for r in f.evidence_refs if r not in scope.disclosed_refs]
        if unknown:
            issues.append(VerificationIssue(code="unverified_evidence_ref", severity="critical",
                                            detail=f"{f.finding_id} cites ids never returned by tools: {unknown[:3]}"))
        for c in f.quantitative_claims:
            checked += 1
            if not _claim_ok(c.metric, c.value, f.evidence_refs, scope):
                failed += 1
                issues.append(VerificationIssue(code="quantitative_claim_mismatch", severity="critical",
                                                detail=f"{f.finding_id}: {c.metric}={c.value} does not match system data"))
    return issues, checked, failed


def verify_decision(decision: DecisionOutput, findings: list[Finding], policy: PolicyOutput | None, retrieved_ids: set[str],
                    scope: CaseScope) -> VerificationReport:
    issues, checked, failed = verify_findings(findings, scope)
    grounded = 0
    for kf in decision.key_findings:
        bad = [r for r in kf.evidence_refs if r not in scope.disclosed_refs]
        if bad:
            issues.append(VerificationIssue(code="unverified_evidence_ref", severity="critical",
                                            detail=f"key finding cites unknown ids: {bad[:3]}"))
        else:
            grounded += 1
    invalid_cites = [c for c in decision.citations if c not in retrieved_ids]
    if invalid_cites:
        issues.append(VerificationIssue(code="invalid_citation", severity="critical",
                                        detail=f"citations not in retrieved (ACL-filtered) context: {invalid_cites[:3]}"))
    if policy:
        pol_bad = [c for g in policy.applicable_guidance + policy.approval_requirements for c in g.chunk_ids if c not in retrieved_ids]
        if pol_bad:
            issues.append(VerificationIssue(code="invalid_policy_citation", severity="critical", detail=str(pol_bad[:3])))
    pii = contains_pii(decision.narrative) + contains_pii(decision.summary)
    if pii:
        issues.append(VerificationIssue(code="pii_in_output", severity="critical", detail=f"narrative contains {sorted(set(pii))}"))
    aggravating = [f for f in findings if f.direction == "aggravating"]
    if decision.action.value == "CLOSE_FALSE_POSITIVE":
        if scope.alert.risk_score is not None and scope.alert.risk_score >= 0.5:
            issues.append(VerificationIssue(code="close_contradicts_model", severity="critical",
                                            detail=f"closure recommended with risk score {scope.alert.risk_score:.2f}"))
        if len(aggravating) >= 2:
            issues.append(VerificationIssue(code="close_with_unresolved_aggravating_findings", severity="critical",
                                            detail=f"{len(aggravating)} aggravating findings"))
    if decision.action.value in ("ESCALATE_STR", "RESTRICT_ACCOUNT") and not aggravating:
        issues.append(VerificationIssue(code="escalation_without_aggravating_evidence", severity="critical", detail="no aggravating findings"))
    if scope.injection_flags:
        issues.append(VerificationIssue(code="prompt_injection_in_case_data", severity="warning",
                                        detail=f"{len(scope.injection_flags)} payment references contained instruction-like text"))
    n_kf = max(1, len(decision.key_findings))
    return VerificationReport(passed=not any(i.severity == "critical" for i in issues), issues=issues, groundedness=grounded / n_kf,
                              citation_validity=1.0 - len(invalid_cites) / max(1, len(decision.citations)),
                              quantitative_claims_checked=checked, quantitative_claims_failed=failed)
