"""Deterministic, rule-based reference implementations of each LLM prompt.

Purpose: run the full workflow offline (local dev, CI, evaluation of orchestration + safety layers)
without an API key. They consume the same structured payload the LLM receives and must satisfy the
same output schemas and verifier. Their decision quality is a BASELINE, not a claim about Claude.
"""
from __future__ import annotations

RULE_TYPOLOGY = {"R01_STRUCTURING": "STRUCTURING", "R03_HIGH_RISK_JURISDICTION": "HIGH_RISK_JURISDICTION",
                 "R06_DORMANT_REACTIVATION": "DORMANT_REACTIVATION"}


def simulate_triage(p: dict) -> dict:
    f, rule = p["features"], p["rule_code"]
    typ = [RULE_TYPOLOGY[rule]] if rule in RULE_TYPOLOGY else []
    if rule in ("R02_RAPID_MOVEMENT", "R04_VELOCITY_SPIKE"):
        typ.append("MONEY_MULE" if f.get("crypto_out_amount_30d", 0) > 0 or f.get("unique_counterparties_in_30d", 0) >= 10 else "UNUSUAL_VOLUME")
    if rule == "R05_LARGE_CASH":
        typ.append("FUNNEL_ACCOUNT" if p["segment"] == "sme" else "STRUCTURING")
    if f.get("high_risk_country_amount_30d", 0) > 0 and "HIGH_RISK_JURISDICTION" not in typ:
        typ.append("HIGH_RISK_JURISDICTION")
    s = p.get("risk_score") or 0.0
    priority = "CRITICAL" if s >= 0.7 else "HIGH" if s >= 0.4 else "MEDIUM" if s >= 0.15 else "LOW"
    depth = "enhanced" if s >= 0.4 or p["kyc_risk_rating"] == "high" or (p.get("anomaly_percentile") or 0) >= 0.95 else "standard"
    return {"typology_hypotheses": typ[:3] or ["UNUSUAL_VOLUME"], "priority": priority, "investigation_depth": depth,
            "rationale": f"Rule {rule} with calibrated risk {s:.2f}; hypotheses derived from rule semantics and behavioural features."}


def _plan(p: dict) -> list[tuple[str, dict]]:
    typ = set(p["typologies"])
    plan = [("get_alert_features", {}), ("get_customer_profile", {}), ("get_transactions", {"days": 30, "limit": 40}),
            ("get_counterparty_summary", {"days": 30, "top_n": 10}), ("get_prior_alerts", {})]
    if p["depth"] == "enhanced":
        if typ & {"STRUCTURING", "FUNNEL_ACCOUNT"}:
            plan.append(("get_transactions", {"days": 60, "channel": "cash_deposit", "limit": 40}))
        if typ & {"MONEY_MULE", "HIGH_RISK_JURISDICTION", "DORMANT_REACTIVATION", "UNUSUAL_VOLUME"}:
            plan.append(("get_transactions", {"days": 60, "direction": "out", "min_amount": 500, "limit": 40}))
    return plan


def simulate_investigation(p: dict) -> dict:
    done = [(h["tool"], h["arguments"]) for h in p["history"]]
    for tool, args in _plan(p):
        if (tool, args) not in done and p["steps_remaining"] > 0:
            return {"action": "call_tool", "tool_call": {"tool": tool, "arguments": args, "reason": "planned evidence gathering"}, "findings": None}
    res = {}
    txns: dict[str, dict] = {}
    for h in p["history"]:
        res.setdefault(h["tool"], h["result"])
        if h["tool"] == "get_transactions":
            for t in h["result"].get("transactions", []):
                txns[t["ref"]] = t
    feats = res.get("get_alert_features", {}).get("features", {})
    profile = res.get("get_customer_profile", {})
    alert_ref = res.get("get_alert_features", {}).get("alert_ref")
    findings: list[dict] = []

    def add(statement, direction, refs, claims=None):
        if refs:
            findings.append({"finding_id": f"F{len(findings) + 1}", "statement": statement[:400], "direction": direction,
                             "evidence_refs": list(dict.fromkeys(refs))[:25], "quantitative_claims": claims or []})

    # claims are computed over exactly the transactions cited (schema caps evidence refs at 25)
    near = sorted([t for t in txns.values() if t["channel"] == "cash_deposit" and 7500 <= t["amount_eur"] < 10000],
                  key=lambda t: -t["amount_eur"])[:25]
    if len(near) >= 2:
        branches = {t["counterparty"] for t in near}
        amt = round(sum(t["amount_eur"] for t in near), 2)
        add(f"{len(near)} cash deposits between EUR7,500 and EUR9,999 totalling EUR{amt:,.2f} across {len(branches)} branch(es).",
            "aggravating" if len(branches) > 1 or len(near) >= 4 else "neutral", [t["ref"] for t in near],
            [{"metric": "sum_amount_eur_of_refs", "value": amt}, {"metric": "count_of_refs", "value": len(near)}])
    all_exits = [t for t in txns.values() if t["direction"] == "out" and t["channel"] in ("crypto_exchange", "intl_wire")]
    exits = sorted(all_exits, key=lambda t: -t["amount_eur"])[:25]
    if exits:
        amt = round(sum(t["amount_eur"] for t in exits), 2)
        hr = [t for t in exits if t["country"] in ("KP", "IR", "MM", "SY", "YE")]
        small_regular = all(t["amount_eur"] <= 2500 for t in exits) and len(exits) <= 3
        add(f"{len(exits)} {'largest ' if len(all_exits) > len(exits) else ''}outbound crypto-exchange/international transfers totalling EUR{amt:,.2f}"
            + (f", {len(hr)} to high-risk third countries" if hr else "") + ".",
            "neutral" if small_regular and not (hr and amt > 5000) else "aggravating", [t["ref"] for t in exits],
            [{"metric": "sum_amount_eur_of_refs", "value": amt}])
    pt = feats.get("pass_through_ratio_7d", 0)
    if feats.get("total_in_7d", 0) >= 5000:
        add(f"Pass-through ratio over 7 days is {pt:.2f} on inflows of EUR{feats['total_in_7d']:,.2f}.",
            "aggravating" if pt >= 0.8 and feats.get("unique_counterparties_in_30d", 0) >= 8 else "neutral",
            ["FEATURE:pass_through_ratio_7d", "FEATURE:total_in_7d"],
            [{"metric": "pass_through_ratio_7d", "value": pt}, {"metric": "total_in_7d", "value": feats["total_in_7d"]}])
    ubr = feats.get("unique_cash_branches_30d", 0)
    if ubr >= 4:
        add(f"Cash deposited across {int(ubr)} different branches in 30 days.", "aggravating", ["FEATURE:unique_cash_branches_30d"],
            [{"metric": "unique_cash_branches_30d", "value": ubr}])
    ucp = feats.get("unique_counterparties_in_30d", 0)
    if ucp >= 12:
        add(f"Funds received from {int(ucp)} distinct counterparties in 30 days.", "aggravating",
            ["FEATURE:unique_counterparties_in_30d"], [{"metric": "unique_counterparties_in_30d", "value": ucp}])
    ratio = feats.get("turnover_vs_expected_30d", 0)
    if profile:
        direction = "aggravating" if ratio >= 2.5 else ("mitigating" if ratio <= 1.3 else "neutral")
        add(f"30-day inflows are {ratio:.2f}x the declared expected monthly turnover for a {profile.get('segment')} customer "
            f"({profile.get('occupation')}, KYC risk {profile.get('kyc_risk_rating')}).", direction,
            [profile["profile_ref"], "FEATURE:turnover_vs_expected_30d"], [{"metric": "turnover_vs_expected_30d", "value": ratio}])
    cp = res.get("get_counterparty_summary", {})
    tops = cp.get("top", [])
    if tops and profile.get("segment") == "sme":
        payroll_like = [i for i in tops if i["direction"] == "out" and i["channel"] == "sepa" and i["count"] >= 1]
        if payroll_like and not exits:
            add("Outflows are domestic SEPA payments to recurring suppliers/payroll, consistent with trading activity.", "mitigating",
                [i["counterparty"] for i in payroll_like][:5])
    if feats.get("high_risk_country_amount_30d", 0) > 0:
        add(f"EUR{feats['high_risk_country_amount_30d']:,.2f} exchanged with high-risk third countries in 30 days.",
            "aggravating" if feats["high_risk_country_amount_30d"] > 5000 else "neutral", ["FEATURE:high_risk_country_amount_30d"],
            [{"metric": "high_risk_country_amount_30d", "value": feats["high_risk_country_amount_30d"]}])
    if feats.get("dormancy_gap_days", 0) >= 60:
        add(f"Account showed a {feats['dormancy_gap_days']:.0f}-day gap before the current activity.", "aggravating",
            ["FEATURE:dormancy_gap_days"], [{"metric": "dormancy_gap_days", "value": feats["dormancy_gap_days"]}])
    prior = res.get("get_prior_alerts", {})
    if prior.get("prior_alert_count"):
        add(f"{prior['prior_alert_count']} prior alerts on this customer.", "neutral", [a["alert"] for a in prior["alerts"]][:10])
    inj = [t["ref"] for t in txns.values() if t.get("injection_flags")]
    if inj:
        add("Payment references contain instruction-like text attempting to influence the review.", "aggravating", inj)
    if not findings and alert_ref:
        add("Rule trigger reviewed; no additional red flags found in 30-day activity.", "mitigating", [alert_ref])
    return {"action": "finish", "tool_call": None, "findings": findings[:12]}


def simulate_policy(p: dict) -> dict:
    src = p["sources"]
    agg = [f for f in p["findings"] if f["direction"] == "aggravating"]
    guidance, flags, approvals = [], [], []
    for s in src:
        dt, sec = s["doc_type"], s["section"].lower()
        if dt in ("typology", "regulation_summary") and ("red flag" in sec or "indicator" in sec or "escalation" in sec):
            guidance.append({"summary": f"{s['title']}: {s['section']}", "chunk_ids": [s["chunk_id"]], "relevance": "supports_suspicion"})
            if agg:
                flags.append({"indicator": s["section"][:200], "finding_ids": [f["finding_id"] for f in agg][:5], "chunk_ids": [s["chunk_id"]]})
        elif "legitimate" in sec or "false positive" in sec or "distinguishing" in sec or dt == "guidance":
            guidance.append({"summary": f"{s['title']}: {s['section']}", "chunk_ids": [s["chunk_id"]], "relevance": "supports_legitimate"})
        elif "approval" in sec or dt == "sop":
            approvals.append({"summary": f"{s['title']}: {s['section']}", "chunk_ids": [s["chunk_id"]], "relevance": "procedural"})
        elif dt in ("policy", "historical_cases", "regulation_summary"):
            guidance.append({"summary": f"{s['title']}: {s['section']}", "chunk_ids": [s["chunk_id"]], "relevance": "procedural"})
    risk = p.get("risk_score") or 0
    consideration = "escalation_indicated" if len(agg) >= 3 and risk >= 0.5 else "consider_escalation" if agg else "not_indicated"
    return {"applicable_guidance": guidance[:8], "red_flags_matched": flags[:10], "approval_requirements": approvals[:4],
            "reporting_consideration": consideration}


def simulate_decision(p: dict) -> dict:
    findings = p["findings"]
    agg = [f for f in findings if f["direction"] == "aggravating"]
    mit = [f for f in findings if f["direction"] == "mitigating"]
    risk, thr = p.get("risk_score") or 0.0, p.get("model_threshold") or 0.2
    typ = set(p["typologies"])
    crypto = p["features"].get("crypto_out_amount_30d", 0)
    if risk >= 0.6 and len(agg) >= 2:
        action = "RESTRICT_ACCOUNT" if "MONEY_MULE" in typ and crypto >= 5000 and risk >= 0.85 else "ESCALATE_STR"
    elif risk >= thr or len(agg) >= 2:
        action = "ENHANCED_MONITORING"
    else:
        action = "CLOSE_FALSE_POSITIVE"
    if action == "CLOSE_FALSE_POSITIVE" and agg:
        action = "ENHANCED_MONITORING"
    if action in ("ESCALATE_STR", "RESTRICT_ACCOUNT") and not agg:
        action = "ENHANCED_MONITORING"
    chosen = (agg + mit + findings)[:6] if action != "CLOSE_FALSE_POSITIVE" else (mit + findings)[:6]
    seen, key = set(), []
    for f in chosen:
        if f["finding_id"] not in seen:
            seen.add(f["finding_id"])
            key.append({"statement": f["statement"], "evidence_refs": f["evidence_refs"]})
    cites = []
    for g in p["policy"].get("applicable_guidance", []) + p["policy"].get("approval_requirements", []):
        want = "supports_legitimate" if action == "CLOSE_FALSE_POSITIVE" else "supports_suspicion"
        if g["relevance"] in (want, "procedural"):
            cites.extend(g["chunk_ids"])
    cites = list(dict.fromkeys(cites))[:6]
    margin = abs(risk - (0.6 if action in ("ESCALATE_STR", "RESTRICT_ACCOUNT") else thr))
    conf = round(min(0.95, 0.55 + margin + 0.05 * len(agg if action != "CLOSE_FALSE_POSITIVE" else mit)), 2)
    what = "; ".join(f["statement"].rstrip(".") for f in agg[:4]) or "no aggravating indicators beyond the rule trigger"
    mitig = "; ".join(f["statement"].rstrip(".") for f in mit[:3]) or "none identified"
    narrative = (f"Case {p['alert_ref']} (rule {p['rule_code']}, review window ending {p['window_end']}). "
                 f"WHAT: {what}. MITIGATING: {mitig}. "
                 f"MODEL SIGNALS: calibrated risk score {risk:.2f} against operating threshold {thr:.2f}; anomaly percentile "
                 f"{(p.get('anomaly_percentile') or 0):.2f}. POLICY: guidance cited {', '.join(cites) or 'none'}. "
                 f"ASSESSMENT: recommended {action.replace('_', ' ').lower()} based on the evidence above; this is decision support for human review.")
    return {"action": action, "confidence": conf, "summary": f"Recommend {action} with {len(agg)} aggravating and {len(mit)} mitigating findings.",
            "narrative": narrative[:4000], "key_findings": key or [{"statement": "Rule trigger reviewed.", "evidence_refs": [p["alert_ref"]]}],
            "citations": cites, "mitigating_factors": [f["statement"][:200] for f in mit][:5],
            "residual_uncertainty": "Source of funds not documented; offline reference agent cannot request customer evidence."}
