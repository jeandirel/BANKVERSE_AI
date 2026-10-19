"""Agentic AI API: run an agent, inspect its trace, approve or reject the actions it proposes.
Agents assist; any action with an effect runs only after a human approval, with the approver's permissions."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bankverse.auth.deps import Principal, client_ip, require, tenant_db
from bankverse.core.config import get_settings
from bankverse.core.errors import AppError, Conflict, Forbidden, NotFound
from bankverse.db.models.tenant import AgentApproval, AgentRun, AgentStep
from bankverse.modules.agents import graph
from bankverse.modules.agents import tools as T
from bankverse.modules.copilot import guardrails, llm
from bankverse.services import audit, usage

router = APIRouter(prefix="/agents", tags=["agents"])
NOTICE = ("Les agents assistent l'analyse : ils lisent des données autorisées et proposent des actions. Aucune action n'est exécutée "
          "sans approbation humaine ; aucun agent n'effectue de virement ni de décision réglementaire ou de crédit.")


def _audit(p: Principal, request: Request, action: str, **kw: Any) -> None:
    audit.record(action, org_id=p.org_id, actor_user_id=p.user_id, actor_type=p.kind, actor_label=p.label, ip=client_ip(request), **kw)


@router.get("")
def catalogue(p: Principal = Depends(require("agents.use"))) -> dict[str, Any]:
    return {"agents": [{"key": a.key, "label": {"fr": a.label_fr, "en": a.label_en}, "description": {"fr": a.description_fr, "en": a.description_en},
                        "entity_type": a.entity_type, "available": p.can(a.permission)} for a in graph.AGENTS.values()],
            "tools": [{"name": t.name, "kind": t.kind, "permission": t.permission, "label": {"fr": t.label_fr, "en": t.label_en},
                       "description": t.description, "allowed": p.can(t.permission)} for t in T.TOOLS.values()],
            "limits": {"max_steps": get_settings().agent_max_steps, "timeout_seconds": get_settings().agent_timeout_seconds},
            "planner": "deterministic" + ("+llm" if llm.available() else ""), "notice": NOTICE}


class RunIn(BaseModel):
    request: str = Field(min_length=3, max_length=2000)
    agent: str | None = Field(default=None, max_length=48)
    entity_type: Literal["fraud_alert", "aml_alert", "credit_application", "customer", "case", "series"] | None = None
    entity_id: str | None = Field(default=None, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)


def _ctx(p: Principal, db: Session, request: Request | None, run: AgentRun, counter: list[int]) -> T.ToolContext:
    max_steps = get_settings().agent_max_steps

    def log(tool: str, inp: dict[str, Any], out: dict[str, Any] | None, status: str, ms: int, note: str | None) -> None:
        counter[0] += 1
        db.add(AgentStep(org_id=p.org_id, run_id=run.id, ordinal=counter[0], node="tool", tool=tool, tool_input=inp,
                         tool_output=_truncate(out), status=status, duration_ms=ms, note=note))
        if counter[0] >= max_steps + 4:  # hard ceiling (plans use at most max_steps tools; approvals add executions)
            raise RuntimeError("step budget exhausted")

    return T.ToolContext(db=db, principal=p, request=request, log=log)


def _truncate(out: dict[str, Any] | None, limit: int = 20_000) -> dict[str, Any] | None:
    import json

    if out is None:
        return None
    s = json.dumps(out, default=str, ensure_ascii=False)
    return out if len(s) <= limit else {"truncated": True, "preview": s[:limit]}


@router.post("/runs", status_code=201)
def start_run(body: RunIn, request: Request, p: Principal = Depends(require("agents.use")), db: Session = Depends(tenant_db)) -> dict[str, Any]:
    if p.user_id is None:
        raise Forbidden("Agents require a user session")
    if body.agent and body.agent not in graph.AGENTS:
        raise NotFound("Unknown agent")
    usage.consume(p.org_id, "agent_runs")
    run = AgentRun(org_id=p.org_id, user_id=p.user_id, request=guardrails.mask_pii(body.request), agent=body.agent, status="running",
                   state={"entity_type": body.entity_type, "entity_id": body.entity_id, "params": body.params})
    db.add(run)
    db.flush()
    _audit(p, request, "agent.run_started", resource_type="agent_run", resource_id=str(run.id), details={"agent": body.agent})
    counter = [0]
    ctx = _ctx(p, db, request, run, counter)
    t0 = time.perf_counter()
    try:
        state = graph.run(ctx, {"request": body.request, "agent": body.agent, "entity_type": body.entity_type, "entity_id": body.entity_id,
                                "params": body.params, "locale": p.locale})
    except AppError as exc:
        state = {"status": "failed", "message": exc.message, "agent": body.agent}
        run.error = f"{exc.code}: {exc.message}"
    except (TimeoutError, RuntimeError) as exc:
        state = {"status": "failed", "message": str(exc), "agent": body.agent}
        run.error = str(exc)
    run.agent = state.get("agent") or body.agent
    run.status = state["status"] if state["status"] != "needs_input" else "completed"
    run.planner = str(state.get("planner", "deterministic"))[:24]
    run.steps_used = counter[0]
    run.result = {"narrative": state.get("narrative", []), "sources": state.get("sources", []), "skipped": state.get("skipped", []),
                  "message": state.get("message"), "outcome": state["status"], "duration_ms": int((time.perf_counter() - t0) * 1000)}
    run.state = {**run.state, "entity_type": state.get("entity_type"), "entity_id": state.get("entity_id")}
    for prop in state.get("proposals", []):
        db.add(AgentApproval(org_id=p.org_id, run_id=run.id, action=prop["tool"], action_input={**prop["input"], "_rationale": prop["rationale"]},
                             requested_by=p.user_id))
    if run.status in ("completed", "failed", "blocked", "refused"):
        run.finished_at = datetime.now(UTC)
    if run.status in ("blocked", "refused"):
        _audit(p, request, f"agent.{run.status}", resource_type="agent_run", resource_id=str(run.id), outcome="denied",
               details={"message": state.get("message")})
    db.flush()
    return detail(run.id, p, db)


@router.get("/runs")
def list_runs(p: Principal = Depends(require("agents.use")), db: Session = Depends(tenant_db), mine: bool = True,
              status: str | None = Query(default=None, max_length=24), limit: int = Query(default=50, le=200)) -> list[dict[str, Any]]:
    q = select(AgentRun)
    if mine or not p.can("agents.approve"):
        q = q.where(AgentRun.user_id == p.user_id)
    if status:
        q = q.where(AgentRun.status == status)
    pend = dict(db.execute(select(AgentApproval.run_id, func.count()).where(AgentApproval.status == "pending").group_by(AgentApproval.run_id)).all())
    return [{"id": str(r.id), "agent": r.agent, "request": r.request, "status": r.status, "created_at": r.created_at, "finished_at": r.finished_at,
             "steps_used": r.steps_used, "pending_approvals": pend.get(r.id, 0), "planner": r.planner}
            for r in db.execute(q.order_by(AgentRun.created_at.desc()).limit(limit)).scalars()]


@router.get("/runs/{run_id}")
def detail(run_id: uuid.UUID, p: Principal = Depends(require("agents.use")), db: Session = Depends(tenant_db)) -> dict[str, Any]:
    r = db.execute(select(AgentRun).where(AgentRun.id == run_id, AgentRun.org_id == p.org_id)).scalar_one_or_none()
    if r is None or (r.user_id != p.user_id and not p.can("agents.approve")):
        raise NotFound("Run not found")
    steps = db.execute(select(AgentStep).where(AgentStep.run_id == r.id).order_by(AgentStep.ordinal)).scalars().all()
    apps = db.execute(select(AgentApproval).where(AgentApproval.run_id == r.id).order_by(AgentApproval.created_at)).scalars().all()
    spec = graph.AGENTS.get(r.agent or "")
    return {"id": str(r.id), "agent": r.agent, "agent_label": {"fr": spec.label_fr, "en": spec.label_en} if spec else None, "request": r.request,
            "status": r.status, "planner": r.planner, "created_at": r.created_at, "finished_at": r.finished_at, "steps_used": r.steps_used,
            "error": r.error, "state": r.state, "result": r.result,
            "steps": [{"ordinal": s.ordinal, "tool": s.tool, "label": _tool_label(s.tool), "input": s.tool_input, "output": s.tool_output,
                       "status": s.status, "duration_ms": s.duration_ms, "note": s.note, "node": s.node} for s in steps],
            "approvals": [{"id": str(a.id), "action": a.action, "label": _tool_label(a.action),
                           "input": {k: v for k, v in a.action_input.items() if k != "_rationale"}, "rationale": a.action_input.get("_rationale"),
                           "status": a.status, "decided_at": a.decided_at, "comment": a.comment,
                           "can_decide": a.status == "pending" and p.can("agents.approve") and p.can(T.TOOLS[a.action].permission)}
                          for a in apps],
            "notice": NOTICE}


def _tool_label(name: str | None) -> dict[str, str] | None:
    t = T.TOOLS.get(name or "")
    return {"fr": t.label_fr, "en": t.label_en} if t else None


@router.get("/approvals")
def approvals(p: Principal = Depends(require("agents.approve")), db: Session = Depends(tenant_db),
              status: Literal["pending", "approved", "rejected"] = "pending") -> list[dict[str, Any]]:
    rows = db.execute(select(AgentApproval, AgentRun).join(AgentRun, AgentRun.id == AgentApproval.run_id)
                      .where(AgentApproval.status == status).order_by(AgentApproval.created_at.desc()).limit(100)).all()
    return [{"id": str(a.id), "run_id": str(r.id), "agent": r.agent, "action": a.action, "label": _tool_label(a.action),
             "rationale": a.action_input.get("_rationale"), "created_at": a.created_at, "status": a.status,
             "allowed": p.can(T.TOOLS[a.action].permission)} for a, r in rows]


class DecisionIn(BaseModel):
    decision: Literal["approved", "rejected"]
    comment: str = Field(min_length=3, max_length=2000)


@router.post("/approvals/{approval_id}")
def decide(approval_id: uuid.UUID, body: DecisionIn, request: Request, p: Principal = Depends(require("agents.approve")),
           db: Session = Depends(tenant_db)) -> dict[str, Any]:
    a = db.execute(select(AgentApproval).where(AgentApproval.id == approval_id, AgentApproval.org_id == p.org_id)
                   .with_for_update()).scalar_one_or_none()
    if a is None:
        raise NotFound("Approval not found")
    if a.status != "pending":
        raise Conflict(f"Already {a.status}")
    tool = T.TOOLS[a.action]
    if not p.can(tool.permission):
        raise Forbidden(f"Approving this action requires the permission {tool.permission}", code="permission_denied")
    run = db.get(AgentRun, a.run_id)
    assert run is not None
    a.status, a.decided_by, a.decided_at, a.comment = body.decision, p.user_id, datetime.now(UTC), body.comment
    result: dict[str, Any] | None = None
    if body.decision == "approved":
        counter = [run.steps_used]
        ctx = _ctx(p, db, request, run, counter)
        args = {k: v for k, v in a.action_input.items() if k != "_rationale"}
        result = T.call(ctx, a.action, args)  # executes with the APPROVER's permissions, inside this transaction
        run.steps_used = counter[0]
        _audit(p, request, "agent.action_executed", resource_type="agent_run", resource_id=str(run.id),
               details={"action": a.action, "approval_id": str(a.id), "result": result})
    else:
        _audit(p, request, "agent.action_rejected", resource_type="agent_run", resource_id=str(run.id),
               details={"action": a.action, "approval_id": str(a.id), "comment": body.comment})
    db.flush()
    remaining = db.execute(select(func.count()).where(AgentApproval.run_id == run.id, AgentApproval.status == "pending")).scalar_one()
    if remaining == 0:
        run.status = "completed"
        run.finished_at = datetime.now(UTC)
    return {"approval_id": str(a.id), "status": a.status, "result": result, "run_status": run.status}
