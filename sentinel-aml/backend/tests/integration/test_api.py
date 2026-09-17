import uuid

import pytest

H = "/api/v1"


def new_alert(client, headers, tenant_status="NEW", highest=True):
    r = client.get(f"{H}/alerts?status={tenant_status}&page_size=1&sort=risk_desc", headers=headers)
    assert r.status_code == 200
    return r.json()["items"][0]


def test_health_and_metrics(client):
    assert client.get("/health/live").json() == {"status": "alive"}
    r = client.get("/health/ready")
    assert r.status_code == 200 and r.json()["checks"]["model"] == "ok"
    assert "http_requests_total" in client.get("/metrics").text


def test_login_failures_generic_and_lockout(client):
    bad = client.post(f"{H}/auth/token", json={"tenant_id": "liffey", "username": "lara.investigator", "password": "wrong"})
    nouser = client.post(f"{H}/auth/token", json={"tenant_id": "liffey", "username": "nobody", "password": "wrong"})
    assert bad.status_code == nouser.status_code == 401 and bad.json() == nouser.json()
    for _ in range(5):
        client.post(f"{H}/auth/token", json={"tenant_id": "liffey", "username": "lara.investigator", "password": "wrong"})
    assert client.post(f"{H}/auth/token", json={"tenant_id": "liffey", "username": "lara.investigator", "password": "Demo!Passw0rd"}).status_code == 423


def test_authn_authz_and_security_headers(client, token):
    assert client.get(f"{H}/alerts").status_code == 401
    r = client.get(f"{H}/alerts", headers=token("emerald", "alice.analyst"))
    assert r.status_code == 200 and r.headers["x-content-type-options"] == "nosniff" and r.headers["x-request-id"]
    assert client.get(f"{H}/audit", headers=token("emerald", "alice.analyst")).status_code == 403
    assert client.post(f"{H}/kb/documents", headers=token("emerald", "alice.analyst"), json={"content": "x" * 60}).status_code == 403
    assert client.get(f"{H}/alerts", headers=token("emerald", "adam.admin")).status_code == 403  # admins manage platform, not cases


def test_pagination_bounds_and_validation(client, token):
    h = token("emerald", "alice.analyst")
    r = client.get(f"{H}/alerts?page_size=5000&page=1", headers=h).json()
    assert r["page_size"] == 100 and len(r["items"]) <= 100
    assert client.get(f"{H}/alerts?min_score=7", headers=h).status_code == 422
    assert client.get(f"{H}/alerts?status=HACKED", headers=h).status_code == 422
    err = client.post(f"{H}/auth/token", json={"tenant_id": "emerald", "username": "a", "password": "b", "admin": True})
    assert err.status_code == 422 and "request_id" in err.json()["error"]


def test_sql_injection_parameters_are_inert(client, token):
    h = token("emerald", "alice.analyst")
    r = client.get(f"{H}/alerts", headers=h, params={"rule_code": "' OR 1=1 --"})
    assert r.status_code == 200 and r.json()["total"] == 0
    assert client.get(f"{H}/alerts/1' OR '1'='1", headers=h).status_code == 404


def test_tenant_isolation_api(client, token):
    em = new_alert(client, token("emerald", "alice.analyst"))
    assert client.get(f"{H}/alerts/{em['id']}", headers=token("liffey", "liam.analyst")).status_code == 404
    assert client.post(f"{H}/alerts/{em['id']}/investigations", headers=token("liffey", "liam.analyst")).status_code == 404


def test_pii_masked_by_role(client, token):
    a = new_alert(client, token("emerald", "alice.analyst"))
    assert client.get(f"{H}/alerts/{a['id']}", headers=token("emerald", "alice.analyst")).json()["customer"]["full_name"] == "[restricted]"
    assert client.get(f"{H}/alerts/{a['id']}", headers=token("emerald", "ivan.investigator")).json()["customer"]["full_name"] != "[restricted]"


def test_full_hitl_flow_with_four_eyes(client, token):
    analyst, mlro, inv = token("emerald", "alice.analyst"), token("emerald", "maeve.mlro"), token("emerald", "ivan.investigator")
    a = new_alert(client, analyst)
    key = str(uuid.uuid4())
    wf = client.post(f"{H}/alerts/{a['id']}/investigations", headers={**mlro, "Idempotency-Key": key})
    assert wf.status_code == 201, wf.text
    again = client.post(f"{H}/alerts/{a['id']}/investigations", headers={**mlro, "Idempotency-Key": key})
    assert again.json()["id"] == wf.json()["id"]
    det = client.get(f"{H}/investigations/{wf.json()['id']}", headers=analyst).json()
    assert det["recommendation_id"], f"workflow ended {det['workflow']['status']}: {det['workflow']['failure_reason']}"
    rec = client.get(f"{H}/recommendations/{det['recommendation_id']}", headers=analyst).json()
    if rec["status"] != "PENDING_APPROVAL":
        pytest.skip("top alert was auto-applied")
    body = {"decision": "approve", "comment": "Evidence reviewed and verified."}
    if rec["required_role"] == "mlro":
        assert client.post(f"{H}/recommendations/{rec['id']}/decision", headers=inv, json=body).status_code == 403
        # MLRO started the investigation -> four-eyes blocks self-approval
        r = client.post(f"{H}/recommendations/{rec['id']}/decision", headers=mlro, json=body)
        assert r.status_code == 403 and "four-eyes" in r.json()["error"]["message"]
    assert client.post(f"{H}/recommendations/{rec['id']}/decision", headers=analyst, json=body).status_code == 403
    assert client.post(f"{H}/recommendations/{rec['id']}/decision", headers=inv, json={"decision": "approve", "comment": "short"}).status_code == 422
    audit = client.get(f"{H}/audit?entity_id={a['id']}", headers=mlro).json()
    assert any(i["action"] == "investigation_started" for i in audit["items"])
    assert client.get(f"{H}/audit/verify", headers=mlro).json()["valid"]


def test_async_mode_and_worker(client, token, built_env):
    from app.agents.orchestrator import Orchestrator
    from app.worker import process_one
    h = token("emerald", "alice.analyst")
    a = client.get(f"{H}/alerts?status=NEW&page_size=1&sort=newest", headers=h).json()["items"][0]
    r = client.post(f"{H}/alerts/{a['id']}/investigations", headers=h, json={"mode": "async"})
    assert r.status_code == 202 and r.json()["status"] == "QUEUED"
    assert process_one(Orchestrator())
    assert client.get(f"{H}/investigations/{r.json()['id']}", headers=h).json()["workflow"]["status"] != "QUEUED"


def test_kb_upload_poisoned_is_quarantined_via_api(client, token):
    from pathlib import Path
    raw = (Path(__file__).resolve().parents[3] / "data" / "adversarial" / "poisoned_policy_update.md").read_text().replace(
        "emerald-policy-poisoned-update", "emerald-poison-api")
    r = client.post(f"{H}/kb/documents", headers=token("emerald", "adam.admin"), json={"content": raw})
    assert r.status_code == 201 and r.json()["status"] == "QUARANTINED"
    assert client.post(f"{H}/kb/documents/emerald-poison-api/approve", headers=token("emerald", "maeve.mlro")).status_code == 409


def test_ingestion_api_rejects_cross_tenant_and_malformed(client, token):
    h = token("emerald", "adam.admin")
    csv = "transaction_id,tenant_id,account_id,timestamp,amount,currency,direction,channel,counterparty_id,counterparty_country,reference\nT1,liffey,ACC-LI-000001,2026-03-01T00:00:00+00:00,10,EUR,in,sepa,c,IE,\n"
    assert client.post(f"{H}/ingestion/transactions", headers=h, files={"file": ("x.csv", csv, "text/csv")}).status_code == 422
    bad = client.post(f"{H}/ingestion/transactions", headers=h, files={"file": ("x.csv", "just,garbage\n1", "text/csv")}).json()
    assert bad["status"] == "FAILED_SCHEMA"


def test_ops_endpoints(client, token):
    m = token("emerald", "maeve.mlro")
    models = client.get(f"{H}/ops/models", headers=m).json()
    assert any(x["stage"] == "production" for x in models)
    alert = client.get(f"{H}/alerts?page_size=1", headers=m).json()["items"][0]
    pred = client.get(f"{H}/alerts/{alert['id']}", headers=m).json()["prediction"]
    lin = client.get(f"{H}/ops/predictions/{pred['prediction_id']}/lineage", headers=m).json()
    assert lin["model"]["artifact_sha256"] and lin["model"]["training_data_hash"]
    assert client.get(f"{H}/ops/drift", headers=m).json()["status"] in {"stable", "investigate", "significant_drift"}
    assert client.get(f"{H}/ops/llm-usage", headers=m).status_code == 200


def test_database_outage_returns_503_without_leak(client, token, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from app.api import deps
    from app.main import app

    def broken():
        raise OperationalError("SELECT 1", {}, Exception("connection refused password=secret"))
        yield  # pragma: no cover

    app.dependency_overrides[deps.get_db] = broken
    try:
        r = client.get(f"{H}/alerts", headers=token("emerald", "alice.analyst"))
        assert r.status_code == 503 and "secret" not in r.text
    finally:
        app.dependency_overrides.clear()


def test_rate_limit_returns_429(client, token):
    from app.api import middleware
    old = middleware.limiter
    middleware.limiter = middleware.InMemoryRateLimiter(3, 60)
    try:
        h = token("emerald", "alice.analyst")
        codes = [client.get("/health/live", headers=h).status_code for _ in range(5)]
        assert 429 in codes
    finally:
        middleware.limiter = old


def test_login_brute_force_rate_limited(client):
    from app.api import middleware
    old = middleware.login_limiter
    middleware.login_limiter = middleware.InMemoryRateLimiter(3, 60)
    try:
        codes = [client.post(f"{H}/auth/token", json={"tenant_id": "liffey", "username": "nobody", "password": "wrong"}).status_code for _ in range(5)]
        assert codes[:3] == [401, 401, 401] and codes[-1] == 429
    finally:
        middleware.login_limiter = old


def test_oversized_request_rejected(client, token):
    r = client.post(f"{H}/kb/search", headers={**token("emerald", "alice.analyst"), "content-length": "999999999"}, content=b"{}")
    assert r.status_code == 413
