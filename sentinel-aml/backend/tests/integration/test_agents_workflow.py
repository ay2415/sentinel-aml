"""Agent workflow tests including adversarial fault injection. Every failure must fail closed."""
import pytest
from sqlalchemy import select

from app.agents.orchestrator import Orchestrator
from app.agents.tools import CaseScope, ToolExecutor
from app.ai.gateway import LLMGateway
from app.ai.providers import DeterministicProvider, FaultInjectingProvider
from app.core.errors import ConflictError, NotFoundError, ToolPermissionError
from app.database.models import (
    AgentStep,
    Alert,
    AlertStatus,
    AuditLog,
    LLMCall,
    Recommendation,
    RecommendationStatus,
    STRReport,
    ToolInvocation,
    WorkflowStatus,
)
from tests.conftest import principal

DANGEROUS = {"ESCALATE_STR", "RESTRICT_ACCOUNT"}


def run(db, alert, faults=None, p=None):
    prov = FaultInjectingProvider(DeterministicProvider(), faults) if faults else DeterministicProvider()
    wf = Orchestrator(LLMGateway(prov)).run_sync(db, alert.id, p or principal())
    db.flush()
    rec = db.execute(select(Recommendation).where(Recommendation.workflow_id == wf.id)).scalar_one_or_none()
    return wf, rec


def test_happy_path_high_risk_requires_mlro(db, fresh_alert):
    alert = fresh_alert("emerald", highest=True)
    wf, rec = run(db, alert)
    steps = [s.agent_name for s in db.execute(select(AgentStep).where(AgentStep.workflow_id == wf.id).order_by(AgentStep.step_index)).scalars()]
    assert steps[:4] == ["triage_agent", "investigation_agent", "policy_agent", "decision_agent"] and "verifier" in steps
    assert rec.verification["passed"]
    if rec.action.value in DANGEROUS:
        assert wf.status == WorkflowStatus.AWAITING_APPROVAL and rec.required_role == "mlro" and alert.status == AlertStatus.PENDING_APPROVAL
        assert db.execute(select(STRReport).where(STRReport.alert_id == alert.id)).first() is None  # nothing executed pre-approval
    calls = db.execute(select(LLMCall).where(LLMCall.workflow_id == wf.id)).scalars().all()
    assert {c.model for c in calls} == {"claude-sonnet-5", "claude-haiku-4-5-20251001"}  # routing: two tiers used
    assert all(c.prompt_version and c.prompt_sha256 for c in calls)
    assert wf.total_tokens > 0 and wf.total_cost_usd > 0


def test_liffey_never_auto_closes(db, fresh_alert):
    alert = fresh_alert("liffey", highest=False)
    wf, rec = run(db, alert, p=principal("liffey", "liam.analyst"))
    if rec and rec.action.value == "CLOSE_FALSE_POSITIVE":
        assert rec.status == RecommendationStatus.PENDING_APPROVAL


@pytest.mark.parametrize("prompt,mode", [("investigation", "timeout"), ("investigation", "error"), ("decision", "malformed_json"),
                                         ("decision", "schema_invalid"), ("policy", "timeout"), ("investigation", "loop")])
def test_llm_failures_fail_closed_to_manual_review(db, fresh_alert, prompt, mode):
    alert = fresh_alert("emerald", highest=True)
    wf, rec = run(db, alert, {prompt: mode})
    assert wf.status == WorkflowStatus.NEEDS_MANUAL_REVIEW and alert.status == AlertStatus.NEEDS_MANUAL_REVIEW
    assert rec is None
    assert db.execute(select(AuditLog).where(AuditLog.entity_id == wf.id, AuditLog.action == "workflow_escalated_to_human")).first()


def test_triage_failure_degrades_but_continues(db, fresh_alert):
    alert = fresh_alert("emerald", highest=True)
    wf, rec = run(db, alert, {"triage": "error"})
    tri = db.execute(select(AgentStep).where(AgentStep.workflow_id == wf.id, AgentStep.agent_name == "triage_agent")).scalar_one()
    assert tri.status == "degraded" and rec is not None


@pytest.mark.parametrize("mode,issue", [("hallucinated_evidence", "unverified_evidence_ref"), ("fabricated_citation", "invalid_citation"),
                                        ("pii_leak", "pii_in_output")])
def test_decision_output_attacks_blocked_by_verifier(db, fresh_alert, mode, issue):
    alert = fresh_alert("emerald", highest=True)
    wf, rec = run(db, alert, {"decision": mode})
    assert rec is not None and not rec.verification["passed"]
    assert issue in {i["code"] for i in rec.verification["issues"]}
    assert wf.status == WorkflowStatus.NEEDS_MANUAL_REVIEW and rec.status == RecommendationStatus.PENDING_APPROVAL


def test_hallucinated_investigation_evidence_caught(db, fresh_alert):
    alert = fresh_alert("emerald", highest=True)
    wf, rec = run(db, alert, {"investigation": "hallucinated_evidence"})
    codes = {i["code"] for i in rec.verification["issues"]}
    assert {"unverified_evidence_ref", "quantitative_claim_mismatch"} & codes and wf.status == WorkflowStatus.NEEDS_MANUAL_REVIEW


def test_unsafe_close_on_high_risk_is_not_executed(db, fresh_alert):
    alert = fresh_alert("emerald", highest=True)
    wf, rec = run(db, alert, {"decision": "unsafe_close"})
    assert rec.action.value == "CLOSE_FALSE_POSITIVE"
    assert not rec.verification["passed"] and rec.status == RecommendationStatus.PENDING_APPROVAL
    assert alert.status != AlertStatus.CLOSED_FALSE_POSITIVE


def test_tool_permissions_and_scope(db, fresh_alert):
    from app.database.models import WorkflowRun
    alert = fresh_alert("emerald", highest=True)
    db.add(WorkflowRun(id="wf-perm", tenant_id="emerald", alert_id=alert.id, status=WorkflowStatus.RUNNING, started_by="emerald/t",
                       llm_provider="deterministic"))
    db.flush()
    ex = ToolExecutor(db, CaseScope("wf-perm", "emerald", alert))
    with pytest.raises(ToolPermissionError):
        ex.run("decision_agent", "get_transactions", {})
    bad = ex.run("investigation_agent", "get_transactions", {"days": 5000, "customer_id": "CUS-OTHER"})
    assert bad["error"] == "invalid_arguments"  # unbounded window + attempt to widen scope rejected
    ok = ex.run("investigation_agent", "get_transactions", {"days": 30})
    assert all("full_name" not in t and "iban" not in t for t in ok["transactions"])
    denied = db.execute(select(ToolInvocation).where(ToolInvocation.workflow_id == "wf-perm", ToolInvocation.permitted.is_(False))).first()
    assert denied is not None


def test_cannot_investigate_other_tenant_alert_or_twice(db, fresh_alert):
    alert = fresh_alert("emerald", highest=True)
    with pytest.raises(NotFoundError):
        Orchestrator().run_sync(db, alert.id, principal("liffey", "liam.analyst"))
    run(db, alert)
    with pytest.raises(ConflictError):
        Orchestrator().run_sync(db, alert.id, principal())


def test_injection_in_payment_reference_forces_human_review(db):
    from datetime import timedelta

    from app.database.models import Transaction
    cands = db.execute(select(Alert, Transaction.ts).join(Transaction, (Transaction.customer_id == Alert.customer_id))
                       .where(Alert.status == AlertStatus.NEW, Transaction.reference_text.ilike("%instructions%"),
                              Transaction.ts <= Alert.window_end)).all()
    row = next((a for a, ts in cands if ts.replace(tzinfo=None) > (a.window_end - timedelta(days=90)).replace(tzinfo=None)), None)
    if row is None:
        pytest.skip("no injected reference inside an open alert window in this synthetic sample")
    user = "alice.analyst" if row.tenant_id == "emerald" else "liam.analyst"
    wf, rec = run(db, row, p=principal(row.tenant_id, user))
    assert wf.context_flags
    assert rec is None or rec.requires_approval  # never auto-applied
