"""End-to-end offline pipeline: generate -> ingest -> monitor -> train -> score -> index KB -> seed users.

Run: `python -m app.pipeline --all` (or `make pipeline`). Each stage is idempotent.
Production: each stage is a separate job (Azure Container Apps Jobs / Airflow / Azure Data Factory).
"""
from __future__ import annotations

import argparse
import json
import logging
import time

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.database import session
from app.database.models import Alert, Role, Tenant, User

log = logging.getLogger("pipeline")

DEMO_USERS = [  # local demo credentials only; production uses Entra ID (OIDC) instead of local passwords
    ("emerald", "alice.analyst", Role.analyst), ("emerald", "ivan.investigator", Role.investigator),
    ("emerald", "maeve.mlro", Role.mlro), ("emerald", "aoife.auditor", Role.auditor), ("emerald", "adam.admin", Role.admin),
    ("liffey", "liam.analyst", Role.analyst), ("liffey", "lara.investigator", Role.investigator),
    ("liffey", "lorcan.mlro", Role.mlro), ("liffey", "lucy.admin", Role.admin),
]
DEMO_PASSWORD = "Demo!Passw0rd"  # noqa: S105  # nosec B105


def seed_users(db, iterations: int | None = None) -> int:
    from app.security.passwords import hash_password

    if get_settings().environment == "production":
        raise RuntimeError("demo users must never be seeded in production (use Entra ID)")

    n = 0
    for tenant, username, role in DEMO_USERS:
        exists = db.execute(select(User).where(User.tenant_id == tenant, User.username == username)).scalar_one_or_none()
        if not exists:
            db.add(User(tenant_id=tenant, username=username, role=role,
                        password_hash=hash_password(DEMO_PASSWORD, iterations) if iterations else hash_password(DEMO_PASSWORD)))
            n += 1
    db.flush()
    return n


def score_all_alerts(db, only_unscored: bool = True) -> int:
    from app.ml.scoring import model_service

    q = select(Alert)
    if only_unscored:
        q = q.where(Alert.risk_score.is_(None))
    n = 0
    for a in db.execute(q).scalars():
        model_service.score_alert(db, a)
        n += 1
    db.flush()
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    for stage in ("generate", "ingest", "scan", "train", "score", "index", "users", "all", "reset"):
        ap.add_argument(f"--{stage}", action="store_true")
    args = ap.parse_args()
    configure_logging("INFO")
    s = get_settings()
    raw = s.data_dir / "raw"
    eng = session.configure()
    report: dict = {}
    if args.reset:
        from app.database.models import Base
        Base.metadata.drop_all(eng)
    session.init_db(eng)
    run_all = args.all

    def stage(name):
        return run_all or getattr(args, name)

    if stage("generate"):
        from app.data.generator import write_raw
        t = time.time()
        report["generate"] = write_raw(raw)
        report["generate"]["seconds"] = round(time.time() - t, 1)
    if stage("ingest"):
        from app.data.ingestion import ingest_directory
        t = time.time()
        with session.session_scope() as db:
            report["ingest"] = ingest_directory(db, raw)
        report["ingest"]["seconds"] = round(time.time() - t, 1)
    if stage("users"):
        with session.session_scope() as db:
            if db.get(Tenant, "emerald") is None:
                from app.data.ingestion import ensure_tenants
                ensure_tenants(db)
            report["users_created"] = seed_users(db)
    if stage("scan"):
        from app.data.alerting import run_scan
        t = time.time()
        with session.session_scope() as db:
            report["scan"] = run_scan(db)
        report["scan"]["seconds"] = round(time.time() - t, 1)
    if stage("train"):
        from app.ml.train import train
        with session.session_scope() as db:
            r = train(db, raw / "labels.csv")
        report["train"] = {"version": r["version"], "promoted": r["promoted"], "gates": r["metrics"]["promotion_gates"]}
        out = s.data_dir.parent / "evaluation" / "reports" / "ml_training_metrics.json"
        out.write_text(json.dumps(r["metrics"], indent=2, default=str))
    if stage("score"):
        with session.session_scope() as db:
            report["scored"] = score_all_alerts(db)
    if stage("index"):
        from app.rag.indexer import index_directory
        with session.session_scope() as db:
            report["index"] = index_directory(db, s.knowledge_base_dir)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
