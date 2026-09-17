import json

import pytest
from pydantic import ValidationError

from app.agents.schemas import DecisionOutput, Finding, InvestigationStep
from app.ai.gateway import CircuitBreaker, LLMGateway, cost_usd, extract_json
from app.ai.providers import DeterministicProvider, FaultInjectingProvider, Provider, ProviderResponse
from app.core.errors import CircuitOpenError, LLMError
from app.database.models import RecommendedAction


def test_finding_rejects_malformed_refs_and_extra_fields():
    with pytest.raises(ValidationError):
        Finding(finding_id="F1", statement="some statement here", direction="aggravating", evidence_refs=["DROP TABLE alerts;"])
    with pytest.raises(ValidationError):
        InvestigationStep.model_validate({"action": "call_tool", "tool_call": {"tool": "delete_customer", "arguments": {}}})
    with pytest.raises(ValidationError):
        DecisionOutput.model_validate({"action": "WIRE_FUNDS", "confidence": 0.5})
    with pytest.raises(ValidationError):
        InvestigationStep.model_validate({"action": "finish", "findings": [], "sudo": True})


def test_extract_json_handles_fences_and_rejects_arrays():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError):
        extract_json("[1,2]")


def test_cost_calculation():
    assert cost_usd("claude-haiku-4-5-20251001", 1_000_000, 1_000_000) == 6.0
    assert cost_usd("unknown-model", 10, 10) == 0.0


def test_circuit_breaker_opens_and_half_opens():
    cb = CircuitBreaker(threshold=2, reset_seconds=0.05)
    cb.failure("m")
    cb.failure("m")
    with pytest.raises(CircuitOpenError):
        cb.check("m")
    import time
    time.sleep(0.06)
    cb.check("m")  # half-open allows a trial


class FlakyProvider(Provider):
    name = "flaky"

    def __init__(self, outputs):
        self.outputs, self.calls = list(outputs), 0

    def complete(self, req):
        self.calls += 1
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return ProviderResponse(out, 10, 10, True)


def _triage_json():
    return json.dumps({"typology_hypotheses": ["STRUCTURING"], "priority": "HIGH", "investigation_depth": "standard", "rationale": "x"})


def test_gateway_repairs_schema_invalid_output(db):
    from app.agents.schemas import TriageOutput
    prov = FlakyProvider(['{"priority": "ULTRA"}', _triage_json()])
    out, meta = LLMGateway(prov).structured(db, prompt_name="triage", schema=TriageOutput, variables={"payload": "{}"}, sim_payload={},
                                            agent_name="triage_agent", workflow_id=None, tenant_id="emerald")
    assert out.priority.value == "HIGH" and prov.calls == 2


def test_gateway_raises_after_retries_and_records_failure(db):
    from sqlalchemy import select

    from app.agents.schemas import TriageOutput
    from app.database.models import LLMCall
    prov = FlakyProvider([LLMError("down", {"retryable": True})] * 3)
    with pytest.raises(LLMError):
        LLMGateway(prov).structured(db, prompt_name="triage", schema=TriageOutput, variables={"payload": "{}"}, sim_payload={},
                                    agent_name="triage_agent", workflow_id="wf-unit", tenant_id="emerald")
    row = db.execute(select(LLMCall).where(LLMCall.workflow_id == "wf-unit")).scalar_one()
    assert row.status == "error" and not row.schema_valid and row.prompt_version


def test_fault_injector_modes_produce_expected_corruption():
    from app.ai.providers import ProviderRequest
    f = FaultInjectingProvider(DeterministicProvider(), {"triage": "malformed_json"})
    r = f.complete(ProviderRequest("triage", "m", "s", "u", 10, 0, {}, 1, {}))
    with pytest.raises(ValueError):
        extract_json(r.text)


def test_policy_engine_rules(db, fresh_alert):
    from app.agents.schemas import VerificationReport
    from app.database.models import Customer, Role
    from app.services import policy_engine
    alert = fresh_alert("emerald", highest=False)
    cust = db.get(Customer, alert.customer_id)
    ok = VerificationReport(passed=True, issues=[], groundedness=1, citation_validity=1, quantitative_claims_checked=0, quantitative_claims_failed=0)

    def dec(action):
        return DecisionOutput(action=action, confidence=0.8, summary="summary text", narrative="n" * 60,
                              key_findings=[{"statement": "statement text", "evidence_refs": ["ALERT:x"]}])
    assert policy_engine.evaluate(dec(RecommendedAction.ESCALATE_STR), alert, cust, ok, []).required_role == Role.mlro
    assert policy_engine.evaluate(dec(RecommendedAction.RESTRICT_ACCOUNT), alert, cust, ok, []).required_role == Role.mlro
    inj = policy_engine.evaluate(dec(RecommendedAction.ENHANCED_MONITORING), alert, cust, ok, [{"tx": "t"}])
    assert inj.requires_approval and "prompt_injection_indicators_in_case_data" in inj.reasons
    bad = ok.model_copy(update={"passed": False})
    assert policy_engine.evaluate(dec(RecommendedAction.ENHANCED_MONITORING), alert, cust, bad, []).requires_approval
    cash_sme = type("C", (), {"segment": "sme", "kyc_risk_rating": "low", "pep_flag": False})()
    alert_cash = type("A", (), {"tenant_id": "emerald", "risk_score": 0.001, "anomaly_score": 0.1, "features": {"cash_in_share_30d": 0.8}})()
    assert "cash_intensive_business_requires_human_closure" in policy_engine.evaluate(dec(RecommendedAction.CLOSE_FALSE_POSITIVE), alert_cash, cash_sme, ok, []).reasons
    alert_l = type("A", (), {"tenant_id": "liffey", "risk_score": 0.0, "anomaly_score": 0.0, "features": {}})()
    assert "tenant_policy_disallows_auto_close" in policy_engine.evaluate(dec(RecommendedAction.CLOSE_FALSE_POSITIVE), alert_l, cust, ok, []).reasons
