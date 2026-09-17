"""PostgreSQL-only behaviour. Run with TEST_DATABASE_URL=postgresql+psycopg2://... (CI does)."""
import threading

import pytest
from sqlalchemy import select, text

from app.database import session as dbs
from app.database.models import Alert, AlertStatus, IngestionBatch, Role, WorkflowRun, WorkflowStatus

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def _pg_only(built_env):
    if dbs.get_engine().dialect.name != "postgresql":
        pytest.skip("requires PostgreSQL (set TEST_DATABASE_URL)")


def test_pgvector_extension_and_column_type(db):
    assert db.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector'")).scalar_one()
    coltype = db.execute(text("SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE attrelid='kb_chunks'::regclass AND attname='embedding'")).scalar_one()
    assert coltype == "vector(300)"


def test_dense_retrieval_uses_pgvector_operator(db):
    from sqlalchemy import event

    from app.rag.retriever import AccessContext, Retriever
    seen = []
    eng = db.get_bind()

    def capture(conn, cursor, statement, *a):
        seen.append(statement)

    event.listen(eng, "before_cursor_execute", capture)
    try:
        res, _ = Retriever().search(db, "money mule crypto exchange", AccessContext("emerald", 1), top_k=3, mode="dense")
    finally:
        event.remove(eng, "before_cursor_execute", capture)
    assert res and any("<=>" in s for s in seen)


def test_transactions_loaded_via_copy_path(built_env, db):
    b = db.execute(select(IngestionBatch).where(IngestionBatch.entity == "transactions").order_by(IngestionBatch.started_at)).scalars().first()
    assert b.rows_loaded > 1000 and b.quality_report["already_loaded_ids_skipped"] == 0


def test_skip_locked_workers_never_double_process(built_env):
    from app.agents.orchestrator import Orchestrator
    from app.worker import process_one

    with dbs.session_scope() as db:
        alerts = db.execute(select(Alert).where(Alert.status == AlertStatus.NEW, Alert.tenant_id == "liffey").limit(4)).scalars().all()
        ids = []
        for a in alerts:
            run = WorkflowRun(tenant_id="liffey", alert_id=a.id, status=WorkflowStatus.QUEUED, started_by="liffey/liam.analyst",
                              started_by_role=Role.analyst.value, llm_provider="deterministic")
            a.status = AlertStatus.INVESTIGATING
            db.add(run)
            db.flush()
            ids.append(run.id)
    processed = []
    lock = threading.Lock()

    def worker():
        orch = Orchestrator()
        while process_one(orch):
            with lock:
                processed.append(1)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    with dbs.session_scope() as db:
        runs = db.execute(select(WorkflowRun).where(WorkflowRun.id.in_(ids))).scalars().all()
        assert all(r.status != WorkflowStatus.QUEUED for r in runs)
        steps = db.execute(text("SELECT workflow_id, count(*) FROM agent_steps WHERE workflow_id = ANY(:ids) AND agent_name='triage_agent' GROUP BY workflow_id"),
                           {"ids": ids}).all()
        assert all(c == 1 for _, c in steps)  # each workflow triaged exactly once despite 3 concurrent workers


def test_audit_chain_survives_concurrent_writers(built_env):
    """Regression: concurrent investigations forked the hash chain when using SELECT ... FOR UPDATE."""
    import time

    from app.services import audit
    tenant = "liffey"
    errors = []

    def writer(n):
        db = dbs.get_sessionmaker()()
        try:
            for i in range(10):
                audit.record(db, tenant_id=tenant, actor=f"t{n}", action="load_test", entity_type="x", entity_id=f"{n}-{i}")
                time.sleep(0.001)
            db.rollback()  # business transaction rolls back; autonomous audit entries persist by design
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            db.close()

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    with dbs.session_scope() as db:
        result = audit.verify_chain(db, tenant)
        assert result["valid"], result
        assert db.execute(text("SELECT count(*) FROM audit_log WHERE action='load_test' AND tenant_id=:t"), {"t": tenant}).scalar_one() == 80
