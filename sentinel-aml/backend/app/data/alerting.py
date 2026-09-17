"""Periodic monitoring scan: features -> rules -> deduplicated, point-in-time alerts."""
from __future__ import annotations

import logging
from datetime import timedelta

import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.logging import log_event
from app.data.constants import FEATURE_SET_VERSION, SIM_DAYS, SIM_START
from app.data.features import CustomerArrays, compute_features
from app.data.rules import RULES_VERSION, evaluate
from app.database.models import Alert, AlertStatus, Customer

log = logging.getLogger(__name__)
# first scan at day 60: velocity/dormancy features need >=30 days of prior observed history (see docs/data_pipeline.md)
SUPPRESSION_DAYS = 28  # do not re-alert the same customer+rule while the previous alert window is still recent


def load_customer_frames(db: Session, tenant_id: str | None = None) -> tuple[dict[str, pd.DataFrame], dict[str, dict]]:
    q = "SELECT customer_id, ts, amount_eur, direction, channel, counterparty_id, counterparty_country FROM transactions"
    params = {}
    if tenant_id:
        q += " WHERE tenant_id = :t"
        params["t"] = tenant_id
    df = pd.read_sql(text(q), db.connection(), params=params)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    cq = select(Customer)
    if tenant_id:
        cq = cq.where(Customer.tenant_id == tenant_id)
    customers = {c.id: {"id": c.id, "tenant_id": c.tenant_id, "onboarded_at": c.onboarded_at, "segment": c.segment,
                        "kyc_risk_rating": c.kyc_risk_rating, "pep_flag": c.pep_flag,
                        "expected_monthly_turnover_eur": c.expected_monthly_turnover_eur} for c in db.execute(cq).scalars()}
    return {cid: g for cid, g in df.groupby("customer_id")}, customers


def scan_dates(first_day: int = 60, step: int = 7) -> list[pd.Timestamp]:
    return [pd.Timestamp(SIM_START + timedelta(days=d)) for d in range(first_day, SIM_DAYS, step)]


def run_scan(db: Session, dates: list[pd.Timestamp] | None = None, tenant_id: str | None = None) -> dict:
    frames, customers = load_customer_frames(db, tenant_id)
    dates = dates or scan_dates()
    existing = set(db.execute(select(Alert.dedupe_key)).scalars())
    new_alerts, suppressed = [], 0
    for cid, g in frames.items():
        cust = customers.get(cid)
        if cust is None:
            continue
        arr = CustomerArrays.from_frame(g)
        last_alert: dict[str, pd.Timestamp] = {}
        for as_of in dates:
            feats = compute_features(arr, cust, as_of)
            for rule in evaluate(feats):
                prev = last_alert.get(rule.code)
                if prev is not None and (as_of - prev).days < SUPPRESSION_DAYS:
                    suppressed += 1
                    continue
                key = f"{cust['tenant_id']}:{cid}:{rule.code}:{as_of.date().isoformat()}"
                last_alert[rule.code] = as_of
                if key in existing:
                    continue
                new_alerts.append({
                    "tenant_id": cust["tenant_id"], "customer_id": cid, "rule_code": rule.code, "dedupe_key": key,
                    "triggered_at": as_of.to_pydatetime(), "window_start": (as_of - pd.Timedelta(days=30)).to_pydatetime(),
                    "window_end": as_of.to_pydatetime(), "features": feats, "feature_set_version": FEATURE_SET_VERSION,
                    "rule_details": {"rules_version": RULES_VERSION, "rule_name": rule.name, "description": rule.description,
                                     "evidence": {k: round(feats[k], 2) for k in rule.evidence_keys}},
                    "status": AlertStatus.NEW,
                })
    for a in new_alerts:
        db.add(Alert(**a))
    db.flush()
    summary = {"alerts_created": len(new_alerts), "suppressed": suppressed, "customers_scanned": len(frames), "scan_dates": len(dates)}
    log_event(log, "monitoring_scan_complete", **summary)
    return summary
