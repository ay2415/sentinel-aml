"""Workflow orchestrator: a deterministic state machine, not an LLM.

NEW -> INVESTIGATING -> triage -> investigation (tool loop) -> policy (RAG) -> decision
    -> verification (-> one bounded decision retry with feedback) -> approval policy
    -> AUTO_APPLIED (COMPLETED) | PENDING_APPROVAL (AWAITING_APPROVAL) | NEEDS_MANUAL_REVIEW

Failure handling principle: low-stakes steps may degrade (triage falls back to rules); high-stakes
steps fail closed (the case goes to a human, never to an automatic outcome).
"""
from __future__ import annotations

import logging
import time
import traceback

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.agents import DecisionAgent, InvestigationAgent, PolicyAgent, TriageAgent
from app.agents.schemas import TriageOutput
from app.agents.tools import CaseScope
from app.agents.verifier import verify_decision
from app.ai.gateway import LLMGateway
from app.ai.simulators import simulate_triage
from app.core.config import get_settings
from app.core.context import workflow_id_var
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.logging import log_event
from app.database.models import (
    AgentStep,
    Alert,
    AlertStatus,
    Customer,
    Recommendation,
    RecommendationStatus,
    Role,
    WorkflowRun,
    WorkflowStatus,
    utcnow,
)
from app.ml import registry
from app.observability import metrics as m
from app.observability.tracing import span
from app.security.auth import Principal
from app.services import actions, audit, policy_engine

log = logging.getLogger(__name__)
STARTABLE = {AlertStatus.NEW, AlertStatus.NEEDS_MANUAL_REVIEW}


class Orchestrator:
    def __init__(self, gateway: LLMGateway | None = None):
        self.gw = gateway or LLMGateway()
        self.triage, self.investigation = TriageAgent(self.gw), InvestigationAgent(self.gw)
        self.policy, self.decision = PolicyAgent(self.gw), DecisionAgent(self.gw)

    # ------------------------------------------------------------------ entry points
    def create_run(self, db: Session, alert_id: str, principal: Principal, idempotency_key: str | None,
                   status: WorkflowStatus = WorkflowStatus.RUNNING) -> tuple[WorkflowRun, bool]:
        if idempotency_key:
            existing = db.execute(select(WorkflowRun).where(WorkflowRun.idempotency_key == f"{principal.tenant_id}:{idempotency_key}")).scalar_one_or_none()
            if existing:
                if existing.alert_id != alert_id:
                    raise ConflictError("idempotency key reused for a different alert")
                return existing, False
        alert = db.execute(select(Alert).where(Alert.id == alert_id, Alert.tenant_id == principal.tenant_id)).scalar_one_or_none()
        if alert is None:
            raise NotFoundError("alert not found")  # same response for other-tenant ids: no existence oracle
        if alert.risk_score is None:
            raise ConflictError("alert has not been scored yet")
        if alert.status not in STARTABLE:
            raise ConflictError(f"alert in status {alert.status.value} cannot start an investigation")
        run = WorkflowRun(tenant_id=principal.tenant_id, alert_id=alert.id, status=status, started_by=principal.actor,
                          started_by_role=principal.role.value,
                          idempotency_key=f"{principal.tenant_id}:{idempotency_key}" if idempotency_key else None,
                          llm_provider=self.gw.provider.name)
        alert.status = AlertStatus.INVESTIGATING  # optimistic lock (lock_version) rejects concurrent starts
        db.add(run)
        db.flush()
        audit.record(db, tenant_id=principal.tenant_id, actor=principal.actor, action="investigation_started", entity_type="alert",
                     entity_id=alert.id, details={"workflow_id": run.id, "provider": self.gw.provider.name})
        return run, True

    def execute(self, db: Session, run: WorkflowRun, principal: Principal) -> WorkflowRun:
        workflow_id_var.set(run.id)
        t0 = time.perf_counter()
        alert = db.execute(select(Alert).where(Alert.id == run.alert_id, Alert.tenant_id == run.tenant_id)).scalar_one()
        customer = db.execute(select(Customer).where(Customer.id == alert.customer_id, Customer.tenant_id == run.tenant_id)).scalar_one()
        scope = CaseScope(workflow_id=run.id, tenant_id=run.tenant_id, alert=alert)
        scope.injection_flags.extend(self._window_injection_flags(db, alert))
        run.status = WorkflowStatus.RUNNING
        idx = 0
        tokens, cost = 0, 0.0

        def step(agent: str, status: str, output: dict, started: float, error: str | None = None):
            nonlocal idx
            idx += 1
            lat = (time.perf_counter() - started) * 1000
            db.add(AgentStep(workflow_id=run.id, step_index=idx, agent_name=agent, status=status, output=output, error=error, latency_ms=lat))
            m.AGENT_STEP_LATENCY.labels(agent=agent, status=status).observe(lat / 1000)
            db.flush()

        try:
            # 1. triage (degradable)
            ts = time.perf_counter()
            try:
                with span("agent.triage"):
                    triage, meta = self.triage.run(db, scope, customer)
                tokens += meta["tokens"]
                cost += meta["cost_usd"]
                step("triage_agent", "ok", triage.model_dump(mode="json"), ts)
            except AppError as exc:
                triage = TriageOutput.model_validate(simulate_triage({"rule_code": alert.rule_code, "features": alert.features,
                                                                      "risk_score": alert.risk_score, "anomaly_percentile": alert.anomaly_score,
                                                                      "segment": customer.segment, "kyc_risk_rating": customer.kyc_risk_rating}))
                step("triage_agent", "degraded", {**triage.model_dump(mode="json"), "fallback": "rules"}, ts, exc.message)
            # 2. investigation (fail closed)
            ts = time.perf_counter()
            with span("agent.investigation"):
                findings, usage = self.investigation.run(db, scope, triage)
            tokens += usage["tokens"]
            cost += usage["cost_usd"]
            step("investigation_agent", "ok", {"findings": [f.model_dump() for f in findings], "tool_calls": usage["tool_calls"],
                                               "injection_flags": scope.injection_flags}, ts)
            # 3. policy / RAG
            ts = time.perf_counter()
            role = principal.role if principal.role != Role.admin else Role.analyst
            with span("agent.policy"):
                policy, retrieved, meta = self.policy.run(db, scope, role, triage, findings, customer)
            tokens += meta["tokens"]
            cost += meta["cost_usd"]
            step("policy_agent", "ok", {**policy.model_dump(), "retrieved_chunk_ids": sorted(retrieved)}, ts)
            # 4. decision + 5. verification with one bounded repair loop
            threshold = self._threshold(db)
            feedback, decision, report = None, None, None
            for _attempt in range(get_settings().decision_max_retries + 1):
                ts = time.perf_counter()
                with span("agent.decision"):
                    decision, meta = self.decision.run(db, scope, triage, findings, policy, customer, threshold, feedback)
                tokens += meta["tokens"]
                cost += meta["cost_usd"]
                step("decision_agent", "ok", decision.model_dump(mode="json"), ts)
                ts = time.perf_counter()
                report = verify_decision(decision, findings, policy, retrieved, scope)
                step("verifier", "ok" if report.passed else "failed", report.model_dump(), ts)
                if report.passed:
                    break
                feedback = [i.model_dump() for i in report.issues if i.severity == "critical"]
            # 6. approval policy
            pol = policy_engine.evaluate(decision, alert, customer, report, scope.injection_flags)
            rec = Recommendation(tenant_id=run.tenant_id, workflow_id=run.id, alert_id=alert.id, action=decision.action,
                                 confidence=decision.confidence, narrative=decision.narrative,
                                 key_findings=[k.model_dump() for k in decision.key_findings], citations=decision.citations,
                                 verification=report.model_dump(), requires_approval=pol.requires_approval,
                                 required_role=pol.required_role.value if pol.required_role else None,
                                 policy_reasons=pol.reasons + [pol.policy_version],
                                 status=RecommendationStatus.PENDING_APPROVAL if pol.requires_approval else RecommendationStatus.AUTO_APPLIED)
            db.add(rec)
            db.flush()
            step("policy_engine", "ok", {"requires_approval": pol.requires_approval, "required_role": rec.required_role, "reasons": pol.reasons}, time.perf_counter())
            audit.record(db, tenant_id=run.tenant_id, actor="system:orchestrator", action="recommendation_created", entity_type="recommendation",
                         entity_id=rec.id, details={"action": rec.action.value, "requires_approval": rec.requires_approval,
                                                    "verification_passed": report.passed, "reasons": pol.reasons})
            run.context_flags = sorted({f"injection:{f['tx']}" for f in scope.injection_flags})
            if not report.passed:
                alert.status = AlertStatus.NEEDS_MANUAL_REVIEW
                run.status, run.failure_reason = WorkflowStatus.NEEDS_MANUAL_REVIEW, "verification_failed_after_retry"
            elif pol.requires_approval:
                alert.status = AlertStatus.PENDING_APPROVAL
                run.status = WorkflowStatus.AWAITING_APPROVAL
            else:
                actions.execute(db, rec, actor="system:auto-apply")
                run.status = WorkflowStatus.COMPLETED
        except AppError as exc:
            self._fail_closed(db, run, alert, exc.message, step)
        except Exception as exc:  # noqa: BLE001 - unexpected errors must still leave the case with a human
            log.error("workflow crashed: %s", traceback.format_exc(limit=3))
            self._fail_closed(db, run, alert, f"internal_error:{type(exc).__name__}", step, status=WorkflowStatus.FAILED)
        run.total_tokens, run.total_cost_usd = tokens, round(cost, 6)
        run.latency_ms = (time.perf_counter() - t0) * 1000
        run.finished_at = utcnow()
        db.flush()
        m.WORKFLOWS.labels(status=run.status.value).inc()
        log_event(log, "workflow_finished", workflow_id=run.id, status=run.status.value, latency_ms=round(run.latency_ms, 1), tokens=tokens)
        return run

    def run_sync(self, db: Session, alert_id: str, principal: Principal, idempotency_key: str | None = None) -> WorkflowRun:
        run, created = self.create_run(db, alert_id, principal, idempotency_key)
        return self.execute(db, run, principal) if created else run

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _window_injection_flags(db: Session, alert: Alert) -> list[dict]:
        """Case-level injection flags from ingestion-time scans over the full review window (not just rows an agent viewed)."""
        from datetime import timedelta

        from app.database.models import Transaction
        rows = db.execute(select(Transaction.id, Transaction.reference_flags).where(
            Transaction.tenant_id == alert.tenant_id, Transaction.customer_id == alert.customer_id, Transaction.reference_text != "",
            Transaction.ts > alert.window_end - timedelta(days=90), Transaction.ts <= alert.window_end)).all()
        return [{"tx": tid, "rules": flags, "source": "ingestion_scan"} for tid, flags in rows if flags]

    @staticmethod
    def _threshold(db: Session) -> float:
        mv = registry.get_production(db, "alert_risk")
        try:
            return float(mv.metrics["xgboost_calibrated_test"]["threshold"]) if mv else 0.2
        except (KeyError, TypeError):
            return 0.2

    @staticmethod
    def _fail_closed(db, run, alert, reason, step, status=WorkflowStatus.NEEDS_MANUAL_REVIEW):
        step("orchestrator", "failed", {"escalated_to": "manual_review"}, time.perf_counter(), reason)
        alert.status = AlertStatus.NEEDS_MANUAL_REVIEW
        run.status, run.failure_reason = status, reason[:1000]
        audit.record(db, tenant_id=run.tenant_id, actor="system:orchestrator", action="workflow_escalated_to_human", entity_type="workflow",
                     entity_id=run.id, details={"reason": reason[:300]})
