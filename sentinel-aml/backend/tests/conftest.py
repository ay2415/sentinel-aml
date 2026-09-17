"""Shared fixtures.

Default: an isolated SQLite database built once per session from a small synthetic dataset
(ingest -> scan -> train -> score -> index -> users), so the whole suite runs without services.
PostgreSQL-specific tests (pgvector, COPY, SKIP LOCKED) run when TEST_DATABASE_URL is set.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="sentinel-test-"))
REPO = Path(__file__).resolve().parents[2]
os.environ.update({
    "ENVIRONMENT": "test", "DATABASE_URL": f"sqlite:///{_TMP / 'test.db'}", "MODELS_DIR": str(_TMP / "models"),
    "EMBEDDING_CACHE_DIR": str(REPO / "models" / "embeddings"), "LLM_PROVIDER": "deterministic", "RATE_LIMIT_PER_MINUTE": "100000", "LOGIN_RATE_LIMIT_PER_MINUTE": "100000",
    "JWT_SECRET": "test-secret-0123456789abcdef0123456789abcdef", "LOG_LEVEL": "WARNING", "LLM_MAX_RETRIES": "1",
})
if os.environ.get("TEST_DATABASE_URL"):  # run the entire suite against PostgreSQL + pgvector
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]

import pytest  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.database import session as dbs  # noqa: E402
from app.database.models import Alert, AlertStatus, Role  # noqa: E402

PASSWORD = "Demo!Passw0rd"


@pytest.fixture(scope="session")
def built_env():
    from app.data.alerting import run_scan
    from app.data.generator import GenConfig, write_raw
    from app.data.ingestion import ingest_directory
    from app.ml import registry
    from app.ml.train import train
    from app.pipeline import score_all_alerts, seed_users
    from app.rag.indexer import index_directory

    raw = _TMP / "raw"
    write_raw(raw, GenConfig(seed=11, customers_per_tenant={"emerald": 300, "liffey": 160}, suspicious_rate=0.15))
    eng = dbs.configure()
    if eng.dialect.name == "postgresql":
        from app.database.models import Base
        Base.metadata.drop_all(eng)
    dbs.init_db(eng)
    with dbs.session_scope() as db:
        ingest = ingest_directory(db, raw)
        seed_users(db, iterations=1000)
        scan = run_scan(db)
        result = train(db, raw / "labels.csv")
        if not result["promoted"]:  # small test data may miss gates; tests need a production model
            registry.promote(db, result["model_version_id"])
        score_all_alerts(db)
        index_directory(db, REPO / "data" / "knowledge_base")
    return {"raw": raw, "ingest": ingest, "scan": scan, "train": result, "tmp": _TMP}


@pytest.fixture()
def db(built_env):
    s = dbs.get_sessionmaker()()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture(scope="session")
def client(built_env):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def token(client):
    cache = {}

    def _get(tenant: str, username: str) -> dict:
        if (tenant, username) not in cache:
            r = client.post("/api/v1/auth/token", json={"tenant_id": tenant, "username": username, "password": PASSWORD})
            assert r.status_code == 200, r.text
            cache[(tenant, username)] = {"Authorization": f"Bearer {r.json()['access_token']}"}
        return cache[(tenant, username)]

    return _get


def principal(tenant="emerald", username="alice.analyst", role=Role.analyst):
    from app.security.auth import Principal

    return Principal(user_id="u-" + username, username=username, tenant_id=tenant, role=role)


@pytest.fixture()
def fresh_alert(db):
    """A NEW, scored alert with the highest risk score in emerald (removed from the pool after use)."""
    def _pick(tenant="emerald", highest=True, rule=None):
        q = select(Alert).where(Alert.tenant_id == tenant, Alert.status == AlertStatus.NEW, Alert.risk_score.is_not(None))
        if rule:
            q = q.where(Alert.rule_code == rule)
        q = q.order_by(Alert.risk_score.desc() if highest else Alert.risk_score.asc())
        return db.execute(q.limit(1)).scalar_one()
    return _pick
