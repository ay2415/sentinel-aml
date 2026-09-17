"""Specialised agents. Each has one responsibility, an explicit contract and bounded authority.

| Agent               | Model tier  | Tools                         | Can change data? |
|---------------------|-------------|-------------------------------|------------------|
| TriageAgent         | fast        | none (payload only)           | no               |
| InvestigationAgent  | reasoning   | 5 read-only case-scoped tools | no               |
| PolicyAgent         | fast        | ACL-filtered retrieval (code) | no               |
| DecisionAgent       | reasoning   | none                          | no               |
| Verifier            | none (code) | system-of-record lookups      | no               |
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.agents.schemas import DecisionOutput, Finding, InvestigationStep, PolicyOutput, TriageOutput
from app.agents.tools import TOOL_ARGS, ToolExecutor
from app.ai.gateway import LLMGateway
from app.core.config import get_settings
from app.core.errors import AgentLoopError
from app.rag.retriever import AccessContext, Retriever, build_context
from app.security.rbac import clearance_for

TRIAGE_FEATURES = ["total_in_30d", "total_out_30d", "pass_through_ratio_7d", "near_threshold_cash_count_30d", "cash_in_amount_30d", "unique_cash_branches_30d",
                   "unique_counterparties_in_30d", "high_risk_country_amount_30d", "crypto_out_amount_30d", "turnover_vs_expected_30d",
                   "dormancy_gap_days", "account_age_days"]
TYPOLOGY_QUERIES = {
    "STRUCTURING": "structuring cash deposits below threshold red flags distinguishing legitimate cash-intensive business",
    "MONEY_MULE": "money mule rapid pass-through many senders crypto exchange red flags",
    "HIGH_RISK_JURISDICTION": "high-risk third country enhanced due diligence wires red flags legitimate remittances",
    "FUNNEL_ACCOUNT": "funnel account multiple branch cash deposits large international wires",
    "DORMANT_REACTIVATION": "dormant account reactivation sudden inflows onward transfer",
    "UNUSUAL_VOLUME": "activity inconsistent with customer profile expected turnover ongoing monitoring",
    "NONE_APPARENT": "closure standards false positive evidence",
}


def _dump(obj) -> str:
    return json.dumps(obj, default=str, indent=None)


class TriageAgent:
    name = "triage_agent"

    def __init__(self, gw: LLMGateway):
        self.gw = gw

    def run(self, db: Session, scope, customer) -> tuple[TriageOutput, dict]:
        a = scope.alert
        payload = {"alert_ref": f"ALERT:{a.id}", "rule_code": a.rule_code, "rule_name": a.rule_details.get("rule_name"),
                   "risk_score": a.risk_score, "anomaly_percentile": a.anomaly_score, "segment": customer.segment,
                   "kyc_risk_rating": customer.kyc_risk_rating, "pep_flag": customer.pep_flag,
                   "features": {k: round(float(a.features.get(k, 0)), 3) for k in TRIAGE_FEATURES}}
        return self.gw.structured(db, prompt_name="triage", schema=TriageOutput, variables={"payload": _dump(payload)},
                                  sim_payload=payload, agent_name=self.name, workflow_id=scope.workflow_id, tenant_id=scope.tenant_id)


class InvestigationAgent:
    name = "investigation_agent"

    def __init__(self, gw: LLMGateway):
        self.gw = gw

    def run(self, db: Session, scope, triage: TriageOutput) -> tuple[list[Finding], dict]:
        s = get_settings()
        tools = ToolExecutor(db, scope)
        history: list[dict] = []
        seen_calls: set[str] = set()
        usage = {"tokens": 0, "cost_usd": 0.0, "tool_calls": 0, "llm_calls": 0}
        tool_spec = {name: model.model_json_schema().get("properties", {}) for name, model in TOOL_ARGS.items()}
        for _ in range(s.agent_max_tool_steps + 2):  # hard ceiling on LLM turns
            remaining = s.agent_max_tool_steps - usage["tool_calls"]
            payload = {"alert_ref": f"ALERT:{scope.alert.id}", "typologies": [t.value for t in triage.typology_hypotheses],
                       "depth": triage.investigation_depth, "tools": tool_spec, "max_steps": s.agent_max_tool_steps,
                       "steps_remaining": remaining, "history": history}
            step, meta = self.gw.structured(db, prompt_name="investigation", schema=InvestigationStep,
                                            variables={"payload": _dump(payload), "max_steps": s.agent_max_tool_steps},
                                            sim_payload=payload, agent_name=self.name, workflow_id=scope.workflow_id,
                                            tenant_id=scope.tenant_id)
            usage["tokens"] += meta["tokens"]
            usage["cost_usd"] += meta["cost_usd"]
            usage["llm_calls"] += 1
            if step.action == "finish":
                if not step.findings:
                    raise AgentLoopError("investigation finished without findings")
                return step.findings, usage
            if step.tool_call is None:
                raise AgentLoopError("call_tool without tool_call")
            if remaining <= 0:
                raise AgentLoopError(f"tool budget of {s.agent_max_tool_steps} exhausted without finishing")
            key = f"{step.tool_call.tool}:{json.dumps(step.tool_call.arguments, sort_keys=True)}"
            if key in seen_calls:
                raise AgentLoopError(f"repeated identical tool call {step.tool_call.tool}")
            seen_calls.add(key)
            result = tools.run(self.name, step.tool_call.tool, step.tool_call.arguments)
            usage["tool_calls"] += 1
            history.append({"tool": step.tool_call.tool, "arguments": step.tool_call.arguments, "result": result})
        raise AgentLoopError("investigation exceeded maximum turns")


class PolicyAgent:
    name = "policy_agent"

    def __init__(self, gw: LLMGateway, retriever: Retriever | None = None):
        self.gw, self.retriever = gw, retriever or Retriever()

    def run(self, db: Session, scope, principal_role, triage: TriageOutput, findings: list[Finding], customer):
        ctx = AccessContext(scope.tenant_id, clearance_for(principal_role))  # retrieval runs with the REQUESTING user's clearance
        chunks: dict[str, object] = {}
        queries = [TYPOLOGY_QUERIES[t.value] for t in triage.typology_hypotheses]
        queries.append("alert handling approval matrix closure enhanced monitoring STR approval")
        for q in queries:
            res, _ = self.retriever.search(db, q, ctx, top_k=4)
            for c in res:
                chunks.setdefault(c.chunk_id, c)
        selected = list(chunks.values())[:14]
        sources = [{"chunk_id": c.chunk_id, "title": c.title, "section": c.section, "doc_type": c.doc_type, "text": c.text} for c in selected]
        payload = {"typologies": [t.value for t in triage.typology_hypotheses], "risk_score": scope.alert.risk_score,
                   "findings": [f.model_dump() for f in findings], "segment": customer.segment,
                   "sources": [{k: v for k, v in s.items() if k != "text"} for s in sources]}
        sim = {**payload, "sources": sources}
        out, meta = self.gw.structured(db, prompt_name="policy", schema=PolicyOutput,
                                       variables={"payload": _dump(payload), "context": build_context(selected, max_words=2200)},
                                       sim_payload=sim, agent_name=self.name, workflow_id=scope.workflow_id, tenant_id=scope.tenant_id,
                                       retrieved_chunk_ids=[c.chunk_id for c in selected])
        return out, {c.chunk_id for c in selected}, meta


class DecisionAgent:
    name = "decision_agent"

    def __init__(self, gw: LLMGateway):
        self.gw = gw

    def run(self, db: Session, scope, triage, findings, policy: PolicyOutput, customer, threshold: float, feedback=None):
        a = scope.alert
        payload = {"alert_ref": f"ALERT:{a.id}", "rule_code": a.rule_code, "window_end": a.window_end.date().isoformat(),
                   "risk_score": a.risk_score, "model_threshold": threshold, "anomaly_percentile": a.anomaly_score,
                   "customer": {"segment": customer.segment, "kyc_risk_rating": customer.kyc_risk_rating, "pep_flag": customer.pep_flag},
                   "features": {k: round(float(a.features.get(k, 0)), 3) for k in TRIAGE_FEATURES},
                   "typologies": [t.value for t in triage.typology_hypotheses], "findings": [f.model_dump() for f in findings],
                   "policy": policy.model_dump(), "verification_feedback": feedback}
        return self.gw.structured(db, prompt_name="decision", schema=DecisionOutput, variables={"payload": _dump(payload)},
                                  sim_payload=payload, agent_name=self.name, workflow_id=scope.workflow_id, tenant_id=scope.tenant_id)
