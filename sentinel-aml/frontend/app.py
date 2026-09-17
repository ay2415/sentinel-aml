"""SentinelAML compliance dashboard (Streamlit). Run: streamlit run frontend/app.py"""
from __future__ import annotations

import uuid

import pandas as pd
import streamlit as st
from api_client import Api, ApiError
from pages_config import ROLE_PAGES

st.set_page_config(page_title="SentinelAML", layout="wide")



def api() -> Api:
    return st.session_state["api"]


def show_error(exc: Exception):
    st.error(str(exc))


# ------------------------------------------------------------------ login
if "api" not in st.session_state:
    st.title("SentinelAML")
    st.caption("Alert investigation for financial crime teams. Demo users are listed in the README.")
    with st.form("login"):
        tenant = st.selectbox("Organisation", ["emerald", "liffey"])
        username = st.text_input("Username", "alice.analyst")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in"):
            try:
                client = Api()
                data = client.login(tenant, username, password)
                st.session_state.update(api=client, role=data["role"], tenant=data["tenant_id"], username=username)
                st.rerun()
            except ApiError as exc:
                show_error(exc)
    st.stop()

role = st.session_state["role"]
st.sidebar.markdown(f"**{st.session_state['username']}**  \n{st.session_state['tenant']} · {role}")
page = st.sidebar.radio("Go to", ROLE_PAGES[role])
if st.sidebar.button("Sign out"):
    st.session_state.clear()
    st.rerun()


# ------------------------------------------------------------------ pages
def overview():
    st.header("Overview")
    k = api().get("/api/v1/analytics/kpis")
    c = st.columns(4)
    c[0].metric("Alerts", f"{k['alerts_total']:,}")
    c[1].metric("Open alerts", f"{k['open_alerts']:,}")
    c[2].metric("Waiting for approval", k["pending_approvals"])
    c[3].metric("Investigation cost (est.)", f"${k['investigations']['total_cost_usd']:.2f}")
    left, right = st.columns(2)
    with left:
        st.subheader("Alerts by status")
        st.bar_chart(pd.Series(k["alerts_by_status"]).sort_values())
    with right:
        st.subheader("Rules: volume and risk")
        st.dataframe(pd.DataFrame(k["rules"]).sort_values("alerts", ascending=False), hide_index=True, width='stretch')
    st.subheader("Alert trend by rule")
    t = pd.DataFrame(api().get("/api/v1/analytics/trends"))
    if not t.empty:
        st.line_chart(t.pivot_table(index="date", columns="rule_code", values="alerts", aggfunc="sum").fillna(0))
    st.caption(f"Recommendations by action: {k['recommendations_by_action']}")


def alert_queue():
    st.header("Alert queue")
    f = st.columns(3)
    status = f[0].selectbox("Status", ["", "NEW", "PENDING_APPROVAL", "NEEDS_MANUAL_REVIEW", "MONITORING", "ESCALATED_STR", "CLOSED_FALSE_POSITIVE"])
    priority = f[1].selectbox("Priority", ["", "CRITICAL", "HIGH", "MEDIUM", "LOW"])
    page_no = f[2].number_input("Page", 1, 1000, 1)
    data = api().get("/api/v1/alerts", status=status or None, priority=priority or None, page=page_no, page_size=25)
    st.caption(f"{data['total']:,} alerts, ranked by model risk score")
    df = pd.DataFrame(data["items"])
    if df.empty:
        st.info("No alerts match these filters.")
        return
    st.dataframe(df[["id", "rule_code", "priority", "risk_score", "anomaly_score", "status", "triggered_at"]], hide_index=True, width='stretch')
    alert_id = st.selectbox("Open alert", df["id"])
    a = api().get(f"/api/v1/alerts/{alert_id}")
    left, right = st.columns([3, 2])
    with left:
        st.subheader(f"{a['rule_code']} · {a['status']}")
        st.write(a["rule_details"].get("description"))
        st.json(a["rule_details"].get("evidence", {}))
        st.write("Customer", a["customer"])
    with right:
        p = a["prediction"]
        if p:
            st.metric("Risk score", f"{p['score']:.3f}", help="Calibrated probability from the production model")
            st.caption(f"Model {p['model']['name']} {p['model']['version']} · artifact {p['model']['artifact_sha256'][:12]}")
            st.dataframe(pd.DataFrame(p["top_contributors"]), hide_index=True, width='stretch')
    if a["status"] in ("NEW", "NEEDS_MANUAL_REVIEW") and role in ("analyst", "investigator", "mlro"):
        if st.button("Start investigation"):
            with st.spinner("Agents are gathering evidence"):
                try:
                    wf = api().post(f"/api/v1/alerts/{alert_id}/investigations", headers={"Idempotency-Key": str(uuid.uuid4())})
                    st.success(f"Investigation {wf['status']}")
                    st.session_state["last_workflow"] = wf["id"]
                except ApiError as exc:
                    show_error(exc)
    for w in a["workflows"]:
        st.caption(f"Investigation {w['id']} · {w['status']}")


def investigations():
    st.header("Investigations")
    runs = api().get("/api/v1/investigations", page_size=50)["items"]
    if not runs:
        st.info("No investigations yet. Start one from the alert queue.")
        return
    df = pd.DataFrame(runs)
    st.dataframe(df[["id", "status", "started_by", "total_tokens", "total_cost_usd", "latency_ms", "created_at"]], hide_index=True, width='stretch')
    default = st.session_state.get("last_workflow")
    ids = list(df["id"])
    wid = st.selectbox("Open investigation", ids, index=ids.index(default) if default in ids else 0)
    d = api().get(f"/api/v1/investigations/{wid}")
    if d["context_flags"]:
        st.warning(f"Instruction-like text found in payment references: {len(d['context_flags'])} transaction(s). Human review is required.")
    st.subheader("Agent steps")
    st.dataframe(pd.DataFrame(d["steps"])[["index", "agent", "status", "latency_ms", "error"]], hide_index=True, width='stretch')
    for s in d["steps"]:
        with st.expander(f"{s['index']}. {s['agent']} ({s['status']})"):
            st.json(s["output"])
    st.subheader("Tool calls")
    st.dataframe(pd.DataFrame(d["tool_calls"]), hide_index=True, width='stretch')
    st.subheader("Model calls")
    st.dataframe(pd.DataFrame(d["llm_calls"]).drop(columns=["retrieved_chunk_ids"]), hide_index=True, width='stretch')
    if d["recommendation_id"]:
        render_recommendation(api().get(f"/api/v1/recommendations/{d['recommendation_id']}"))


def render_recommendation(r, decide=False):
    st.subheader(f"Recommendation: {r['action'].replace('_', ' ').title()}")
    c = st.columns(4)
    c[0].metric("Confidence", f"{r['confidence']:.2f}")
    c[1].metric("Verification", "Passed" if r["verification"]["passed"] else "Failed")
    c[2].metric("Groundedness", f"{r['verification']['groundedness']:.2f}")
    c[3].metric("Status", r["status"].replace("_", " ").title())
    if r["requires_approval"]:
        st.info(f"Requires approval by: {r['required_role']}. Reasons: {', '.join(x for x in r['policy_reasons'] if not x.startswith('approval-policy'))}")
    for issue in r["verification"]["issues"]:
        (st.error if issue["severity"] == "critical" else st.warning)(f"{issue['code']}: {issue['detail']}")
    st.markdown("**Narrative**")
    st.write(r["narrative"])
    st.markdown("**Key findings and evidence**")
    for kf in r["key_findings"]:
        st.write(f"- {kf['statement']}  \n  `{', '.join(kf['evidence_refs'][:6])}`")
    st.caption(f"Policy citations: {', '.join(r['citations']) or 'none'}")
    if decide and r["status"] == "PENDING_APPROVAL":
        with st.form(f"decide-{r['id']}"):
            comment = st.text_area("Rationale (required)")
            col = st.columns(2)
            approve = col[0].form_submit_button("Approve and execute")
            reject = col[1].form_submit_button("Reject and send to manual review")
            if approve or reject:
                try:
                    res = api().post(f"/api/v1/recommendations/{r['id']}/decision",
                                     json={"decision": "approve" if approve else "reject", "comment": comment})
                    st.success(f"Recorded: {res['status']} · alert now {res['alert_status']}")
                except ApiError as exc:
                    show_error(exc)


def approvals():
    st.header("Approvals")
    items = api().get("/api/v1/recommendations", status="PENDING_APPROVAL", page_size=50)["items"]
    mine = [r for r in items if r["required_role"] in (role, None) or role == "mlro"]
    st.caption(f"{len(mine)} recommendation(s) waiting for a decision you are authorised to make")
    if not mine:
        st.info("Nothing is waiting for your approval.")
        return
    labels = {f"{r['action']} · confidence {r['confidence']:.2f} · {r['alert_id'][:8]}": r for r in mine}
    render_recommendation(labels[st.selectbox("Recommendation", list(labels))], decide=True)


def knowledge():
    st.header("Knowledge search")
    q = st.text_input("Ask about policy, typologies or procedures", "What evidence is needed to close an alert?")
    mode = st.radio("Retrieval mode", ["hybrid_rerank", "hybrid", "dense", "lexical"], horizontal=True)
    if q:
        res = api().post("/api/v1/kb/search", json={"query": q, "mode": mode, "top_k": 6})
        st.caption(f"Searched {res['meta']['permitted_corpus']} passages you are cleared to see · {res['meta']['latency_ms']} ms")
        for r in res["results"]:
            with st.expander(f"{r['title']} › {r['section']}  ({r['score']:.4f})"):
                st.write(r["text"])
                st.caption(r["chunk_id"])


def ops():
    st.header("Model and AI operations")
    st.subheader("Model versions")
    st.dataframe(pd.DataFrame(api().get("/api/v1/ops/models")), hide_index=True, width='stretch')
    d = api().get("/api/v1/ops/drift", days=30)
    st.subheader(f"Data drift: {d['status'].replace('_', ' ')}")
    st.caption(f"Model {d['model_version']} · last {d['window_days']} days · {d['n_current']} alerts · max PSI {d['max_psi']:.3f}")
    st.bar_chart(pd.DataFrame(d["top_features"]).set_index("feature"))
    u = api().get("/api/v1/ops/llm-usage")
    st.subheader("Language model usage")
    if u["tokens_estimated"]:
        st.caption("Token counts are estimated because the offline reference provider is active.")
    st.dataframe(pd.DataFrame(u["by_agent"]), hide_index=True, width='stretch')
    st.dataframe(pd.DataFrame(u["prompts"]), hide_index=True, width='stretch')


def audit():
    st.header("Audit trail")
    v = api().get("/api/v1/audit/verify")
    (st.success if v["valid"] else st.error)(f"Hash chain {'intact' if v['valid'] else 'BROKEN'} · {v['records_checked']} records checked")
    data = api().get("/api/v1/audit", page_size=100)
    st.dataframe(pd.DataFrame(data["items"]), hide_index=True, width='stretch')


PAGES = {"Overview": overview, "Alert queue": alert_queue, "Investigations": investigations, "Approvals": approvals,
         "Knowledge search": knowledge, "Model and AI operations": ops, "Audit trail": audit}
try:
    PAGES[page]()
except ApiError as exc:
    show_error(exc)
