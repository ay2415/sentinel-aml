"""Async investigation worker.

Local: polls QUEUED workflow_runs using SELECT ... FOR UPDATE SKIP LOCKED, so any number of workers
can run concurrently without double-processing. Production: consume Azure Service Bus messages
(peek-lock, max delivery count, dead-letter queue) and scale with KEDA on queue length.
"""
from __future__ import annotations

import logging
import signal
import time

from sqlalchemy import select

from app.agents.orchestrator import Orchestrator
from app.core.logging import configure_logging
from app.database.models import Role, WorkflowRun, WorkflowStatus
from app.database.session import get_sessionmaker
from app.security.auth import Principal

log = logging.getLogger("worker")
_running = True


def process_one(orch: Orchestrator) -> bool:
    db = get_sessionmaker()()
    try:
        q = select(WorkflowRun).where(WorkflowRun.status == WorkflowStatus.QUEUED).order_by(WorkflowRun.created_at).limit(1)
        if db.bind.dialect.name == "postgresql":
            q = q.with_for_update(skip_locked=True)
        run = db.execute(q).scalar_one_or_none()
        if run is None:
            return False
        tenant, username = run.started_by.split("/", 1)
        principal = Principal(user_id="worker", username=username, tenant_id=tenant, role=Role(run.started_by_role))
        orch.execute(db, run, principal)
        db.commit()
        return True
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("worker iteration failed")
        return False
    finally:
        db.close()


def main() -> None:
    configure_logging()
    orch = Orchestrator()

    def stop(*_):
        global _running
        _running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while _running:
        if not process_one(orch):
            time.sleep(1.0)


if __name__ == "__main__":
    main()
