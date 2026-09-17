"""Read-only, tenant- and case-scoped investigation tools with per-agent permissions.

The LLM chooses WHICH tool to call; code decides WHAT it may see. Every tool:
* is bound to (tenant_id, alert) by the orchestrator, so the model cannot query other customers or tenants;
* validates arguments with bounds (no unbounded scans);
* returns pseudonymised data (no names/IBANs) and wraps customer free text as untrusted;
* is logged in tool_invocations with the evidence ids it disclosed (used later by the verifier).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ToolPermissionError
from app.database.models import Alert, Customer, Prediction, ToolInvocation, Transaction
from app.observability import metrics as m
from app.security import prompt_guard
from app.security.pii import redact_text


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    pass


class TxnArgs(_Args):
    days: int = Field(default=30, ge=1, le=90)
    direction: Literal["in", "out"] | None = None
    channel: str | None = Field(default=None, max_length=30)
    min_amount: float = Field(default=0.0, ge=0)
    limit: int = Field(default=40, ge=1, le=60)


class CounterpartyArgs(_Args):
    days: int = Field(default=30, ge=1, le=90)
    top_n: int = Field(default=10, ge=1, le=15)


TOOL_ARGS: dict[str, type[_Args]] = {"get_alert_features": NoArgs, "get_customer_profile": NoArgs, "get_transactions": TxnArgs,
                                     "get_counterparty_summary": CounterpartyArgs, "get_prior_alerts": NoArgs}
AGENT_TOOL_PERMISSIONS: dict[str, set[str]] = {
    "investigation_agent": set(TOOL_ARGS),
    "triage_agent": set(), "policy_agent": set(), "decision_agent": set(),
}


@dataclass
class CaseScope:
    workflow_id: str
    tenant_id: str
    alert: Alert
    disclosed_refs: set[str] = field(default_factory=set)
    tx_amounts: dict[str, float] = field(default_factory=dict)
    injection_flags: list[dict] = field(default_factory=list)


class ToolExecutor:
    def __init__(self, db: Session, scope: CaseScope):
        self.db, self.scope = db, scope

    def run(self, agent: str, tool: str, raw_args: dict) -> dict:
        t0 = time.perf_counter()
        permitted = tool in AGENT_TOOL_PERMISSIONS.get(agent, set())
        inv = ToolInvocation(workflow_id=self.scope.workflow_id, agent_name=agent, tool_name=tool,
                             arguments=raw_args if isinstance(raw_args, dict) else {}, permitted=permitted, status="denied")
        self.db.add(inv)
        if not permitted:
            m.TOOL_CALLS.labels(agent=agent, tool=tool, status="denied").inc()
            self.db.flush()
            raise ToolPermissionError(f"agent {agent} may not call {tool}")
        try:
            args = TOOL_ARGS[tool].model_validate(raw_args or {})
        except ValidationError as exc:
            inv.status = "invalid_args"
            self.db.flush()
            m.TOOL_CALLS.labels(agent=agent, tool=tool, status="invalid_args").inc()
            return {"error": "invalid_arguments", "detail": exc.errors(include_url=False)[:3]}
        result, refs = getattr(self, f"_{tool}")(args)
        self.scope.disclosed_refs.update(refs)
        inv.status, inv.result_ref_ids, inv.latency_ms = "ok", sorted(refs)[:500], (time.perf_counter() - t0) * 1000
        self.db.flush()
        m.TOOL_CALLS.labels(agent=agent, tool=tool, status="ok").inc()
        return result

    # ---------------------------------------------------------------- tools
    def _get_alert_features(self, _: NoArgs):
        a = self.scope.alert
        pred = self.db.execute(select(Prediction).where(Prediction.alert_id == a.id).order_by(Prediction.created_at.desc())).scalars().first()
        feats = {k: round(float(v), 4) for k, v in a.features.items()}
        refs = {f"FEATURE:{k}" for k in feats} | {f"ALERT:{a.id}"}
        return {"alert_ref": f"ALERT:{a.id}", "rule_code": a.rule_code, "rule_details": a.rule_details,
                "window_end": a.window_end.date().isoformat(), "risk_score": a.risk_score, "anomaly_percentile": a.anomaly_score,
                "model_top_contributors": pred.top_contributors if pred else [], "features": feats}, refs

    def _get_customer_profile(self, _: NoArgs):
        c = self.db.execute(select(Customer).where(Customer.id == self.scope.alert.customer_id,
                                                   Customer.tenant_id == self.scope.tenant_id)).scalar_one()
        age = (self.scope.alert.window_end - c.onboarded_at).days if c.onboarded_at.tzinfo else None
        return {"profile_ref": f"PROFILE:{c.id}", "segment": c.segment, "occupation": c.occupation, "kyc_risk_rating": c.kyc_risk_rating,
                "pep_flag": c.pep_flag, "expected_monthly_turnover_eur": c.expected_monthly_turnover_eur,
                "account_age_days": age, "monitoring_level": c.monitoring_level}, {f"PROFILE:{c.id}"}

    def _txn_query(self, days: int):
        a = self.scope.alert
        return select(Transaction).where(Transaction.tenant_id == self.scope.tenant_id, Transaction.customer_id == a.customer_id,
                                         Transaction.ts > a.window_end - timedelta(days=days), Transaction.ts <= a.window_end)

    def _get_transactions(self, args: TxnArgs):
        q = self._txn_query(args.days)
        if args.direction:
            q = q.where(Transaction.direction == args.direction)
        if args.channel:
            q = q.where(Transaction.channel == args.channel)
        if args.min_amount:
            q = q.where(Transaction.amount_eur >= args.min_amount)
        total = self.db.execute(q.with_only_columns(func.count())).scalar_one()
        rows = self.db.execute(q.order_by(Transaction.amount_eur.desc()).limit(args.limit)).scalars().all()
        out, refs = [], set()
        for t in rows:
            ref = redact_text(t.reference_text)
            flags = [f.rule for f in prompt_guard.scan(ref)]
            if flags and not any(f["tx"] == t.id for f in self.scope.injection_flags):
                self.scope.injection_flags.append({"tx": t.id, "rules": flags, "source": "tool_view"})
                m.INJECTION_FLAGS.labels(source="payment_reference").inc()
            refs.add(f"TX:{t.id}")
            refs.add(f"CP:{t.counterparty_id}")
            self.scope.tx_amounts[f"TX:{t.id}"] = float(t.amount_eur)
            out.append({"ref": f"TX:{t.id}", "ts": t.ts.isoformat(timespec="minutes"), "amount_eur": float(t.amount_eur),
                        "direction": t.direction, "channel": t.channel, "counterparty": f"CP:{t.counterparty_id}",
                        "country": t.counterparty_country,
                        "reference": prompt_guard.wrap_untrusted("payment_reference", ref) if ref else "",
                        "injection_flags": flags})
        return {"matching_transactions": int(total), "returned": len(out), "ordered_by": "amount_desc", "transactions": out}, refs

    def _get_counterparty_summary(self, args: CounterpartyArgs):
        a = self.scope.alert
        q = (select(Transaction.counterparty_id, Transaction.direction, func.count(), func.sum(Transaction.amount_eur),
                    func.min(Transaction.counterparty_country), func.min(Transaction.channel))
             .where(Transaction.tenant_id == self.scope.tenant_id, Transaction.customer_id == a.customer_id,
                    Transaction.ts > a.window_end - timedelta(days=args.days), Transaction.ts <= a.window_end)
             .group_by(Transaction.counterparty_id, Transaction.direction)
             .order_by(func.sum(Transaction.amount_eur).desc()).limit(args.top_n))
        rows = self.db.execute(q).all()
        n_unique = self.db.execute(self._txn_query(args.days).with_only_columns(func.count(func.distinct(Transaction.counterparty_id)))).scalar_one()
        items = [{"counterparty": f"CP:{cp}", "direction": d, "count": int(c), "total_eur": round(float(s), 2), "country": ctry,
                  "channel": ch} for cp, d, c, s, ctry, ch in rows]
        return {"unique_counterparties": int(n_unique), "top": items}, {i["counterparty"] for i in items}

    def _get_prior_alerts(self, _: NoArgs):
        a = self.scope.alert
        rows = self.db.execute(select(Alert).where(Alert.tenant_id == self.scope.tenant_id, Alert.customer_id == a.customer_id,
                                                   Alert.id != a.id, Alert.triggered_at < a.triggered_at)
                               .order_by(Alert.triggered_at.desc()).limit(20)).scalars().all()
        items = [{"alert": f"ALERT:{x.id}", "rule_code": x.rule_code, "date": x.triggered_at.date().isoformat(), "status": x.status.value,
                  "risk_score": x.risk_score} for x in rows]
        return {"prior_alert_count": len(items), "alerts": items}, {i["alert"] for i in items}
