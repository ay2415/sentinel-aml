from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, paginate
from app.api.schemas import Page, WorkflowOut, from_orm
from app.core.errors import NotFoundError
from app.database.models import AgentStep, LLMCall, Recommendation, ToolInvocation, WorkflowRun, WorkflowStatus
from app.security.auth import Principal, require
from app.security.rbac import Permission

router = APIRouter(prefix="/investigations", tags=["investigations"])


@router.get("", response_model=Page[WorkflowOut])
def list_runs(status: WorkflowStatus | None = None, page: int = 1, page_size: int = 25,
              p: Principal = Depends(require(Permission.READ_INVESTIGATIONS)), db: Session = Depends(get_db)):
    q = select(WorkflowRun).where(WorkflowRun.tenant_id == p.tenant_id)
    if status:
        q = q.where(WorkflowRun.status == status)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
    off, size = paginate(page, page_size)
    rows = db.execute(q.order_by(WorkflowRun.created_at.desc()).offset(off).limit(size)).scalars().all()
    return Page(items=[from_orm(WorkflowOut, r) for r in rows], total=total, page=page, page_size=size)


@router.get("/{workflow_id}")
def get_run(workflow_id: str, p: Principal = Depends(require(Permission.READ_INVESTIGATIONS)), db: Session = Depends(get_db)):
    run = db.execute(select(WorkflowRun).where(WorkflowRun.id == workflow_id, WorkflowRun.tenant_id == p.tenant_id)).scalar_one_or_none()
    if run is None:
        raise NotFoundError("investigation not found")
    steps = db.execute(select(AgentStep).where(AgentStep.workflow_id == run.id).order_by(AgentStep.step_index)).scalars().all()
    tools = db.execute(select(ToolInvocation).where(ToolInvocation.workflow_id == run.id).order_by(ToolInvocation.created_at)).scalars().all()
    llm = db.execute(select(LLMCall).where(LLMCall.workflow_id == run.id).order_by(LLMCall.created_at)).scalars().all()
    rec = db.execute(select(Recommendation).where(Recommendation.workflow_id == run.id)).scalar_one_or_none()
    return {
        "workflow": from_orm(WorkflowOut, run).model_dump(), "context_flags": run.context_flags,
        "steps": [{"index": s.step_index, "agent": s.agent_name, "status": s.status, "latency_ms": round(s.latency_ms, 1), "error": s.error,
                   "output": s.output} for s in steps],
        "tool_calls": [{"agent": t.agent_name, "tool": t.tool_name, "arguments": t.arguments, "permitted": t.permitted, "status": t.status,
                        "evidence_ids_disclosed": len(t.result_ref_ids), "latency_ms": round(t.latency_ms, 1)} for t in tools],
        "llm_calls": [{"agent": c.agent_name, "provider": c.provider, "model": c.model, "prompt": f"{c.prompt_name}@{c.prompt_version}",
                       "prompt_sha256": c.prompt_sha256[:12], "status": c.status, "attempts": c.attempts, "input_tokens": c.input_tokens,
                       "output_tokens": c.output_tokens, "tokens_estimated": c.tokens_estimated, "cost_usd": c.cost_usd,
                       "latency_ms": round(c.latency_ms, 1), "retrieved_chunk_ids": c.retrieved_chunk_ids} for c in llm],
        "recommendation_id": rec.id if rec else None,
    }
