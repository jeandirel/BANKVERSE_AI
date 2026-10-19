"""RAG copilot API: cited answers over public annual reports and the bank's private knowledge base."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from bankverse.auth.deps import Principal, client_ip, control_db, require, tenant_db
from bankverse.core.config import get_settings
from bankverse.core.errors import AppError, Conflict, NotFound, ValidationFailed
from bankverse.core.storage import get_store, org_key
from bankverse.db.models.platform import MlModel, ModelVersion, PublicChunk, PublicDocument
from bankverse.db.models.tenant import CopilotConversation, CopilotMessage, Document, KbChunk
from bankverse.ml import registry
from bankverse.modules.copilot import calculator, guardrails, llm, service
from bankverse.modules.documents import process
from bankverse.services import audit, usage

router = APIRouter(prefix="/copilot", tags=["copilot"])
DISCLAIMER = ("Réponses construites à partir de sources citées (rapports annuels publics FinQA et base documentaire de la banque). "
              "Ce n'est ni un conseil en investissement ni une décision : vérifiez les passages cités.")


def _audit(p: Principal, request: Request, action: str, **kw: Any) -> None:
    audit.record(action, org_id=p.org_id, actor_user_id=p.user_id, actor_type=p.kind, actor_label=p.label, ip=client_ip(request), **kw)


@router.get("/status")
def status(p: Principal = Depends(require("copilot.use")), cdb: Session = Depends(control_db), db: Session = Depends(tenant_db)) -> dict[str, Any]:
    m = cdb.execute(select(MlModel).where(MlModel.name == service.MODEL_NAME)).scalar_one_or_none()
    dep = registry.current_deployment(cdb, m.id) if m else None
    mv = cdb.get(ModelVersion, dep.model_version_id) if dep else None
    total = cdb.execute(select(func.count()).select_from(PublicChunk)).scalar_one()
    embedded = cdb.execute(select(func.count()).select_from(PublicChunk).where(PublicChunk.embedding.is_not(None))).scalar_one()
    pages = cdb.execute(select(func.count()).select_from(PublicDocument)).scalar_one()
    kb_docs = db.execute(select(func.count()).select_from(Document).where(Document.doc_type == "kb")).scalar_one()
    kb_chunks = db.execute(select(func.count()).select_from(KbChunk)).scalar_one()
    metrics = mv.metrics if mv else None
    return {"retriever": {"deployed": mv is not None, "version": mv.version if mv else None, "algorithm": mv.algorithm if mv else None,
                          "test_recall_at_3": metrics["fact_retrieval"]["test"]["reranker_lambdarank"]["recall@3"] if metrics else None},
            "public_corpus": {"pages": pages, "chunks": total, "embedded": embedded},
            "private_kb": {"documents": kb_docs, "chunks": kb_chunks},
            "llm": {"available": llm.available(), "provider": llm.provider_label()}, "disclaimer": DISCLAIMER}


@router.get("/evaluation")
def evaluation(p: Principal = Depends(require("copilot.use")), cdb: Session = Depends(control_db)) -> dict[str, Any]:
    m = cdb.execute(select(MlModel).where(MlModel.name == service.MODEL_NAME)).scalar_one_or_none()
    if m is None:
        raise NotFound("No copilot retriever trained yet")
    mv = cdb.execute(select(ModelVersion).where(ModelVersion.model_id == m.id).order_by(ModelVersion.version.desc()).limit(1)).scalar_one()
    return {"version": mv.version, "status": mv.status, "metrics": mv.metrics, "model_card": mv.model_card}


@router.get("/companies")
def companies(p: Principal = Depends(require("copilot.use")), cdb: Session = Depends(control_db)) -> list[dict[str, Any]]:
    rows = cdb.execute(select(PublicDocument.company, func.count(), func.min(PublicDocument.fiscal_year), func.max(PublicDocument.fiscal_year))
                       .group_by(PublicDocument.company).order_by(PublicDocument.company)).all()
    return [{"company": c, "pages": n, "first_year": a, "last_year": b} for c, n, a, b in rows]


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    conversation_id: uuid.UUID | None = None
    scope: Literal["public", "private", "all"] = "all"
    company: str | None = Field(default=None, max_length=12, pattern=r"^[A-Za-z.\-]+$")
    year: int | None = Field(default=None, ge=1990, le=2030)


def _msg(m: CopilotMessage) -> dict[str, Any]:
    return {"id": str(m.id), "role": m.role, "content": m.content, "citations": m.citations, "calculation": m.calculation, "status": m.status,
            "mode": m.mode, "latency_ms": m.latency_ms, "created_at": m.created_at, "meta": m.meta}


@router.post("/ask")
def ask(body: AskIn, request: Request, p: Principal = Depends(require("copilot.use")), db: Session = Depends(tenant_db)) -> dict[str, Any]:
    if p.user_id is None:
        raise ValidationFailed("Conversations require a user session")
    usage.consume(p.org_id, "copilot_queries")
    if body.conversation_id:
        conv = db.execute(select(CopilotConversation).where(CopilotConversation.id == body.conversation_id,
                                                            CopilotConversation.user_id == p.user_id)).scalar_one_or_none()
        if conv is None:
            raise NotFound("Conversation not found")
    else:
        conv = CopilotConversation(org_id=p.org_id, user_id=p.user_id, title=guardrails.mask_pii(body.question)[:120])
        db.add(conv)
        db.flush()
    history = [{"role": m.role, "content": m.content} for m in db.execute(
        select(CopilotMessage).where(CopilotMessage.conversation_id == conv.id).order_by(CopilotMessage.created_at.desc()).limit(6)).scalars()][::-1]
    masked = guardrails.mask_pii(body.question)
    db.add(CopilotMessage(org_id=p.org_id, conversation_id=conv.id, role="user", content=masked, status="answered", mode="user"))
    try:
        res = service.answer(db, body.question, scope=body.scope, company=body.company, year=body.year, history=history)
    except AppError as exc:
        res = {"status": "unavailable", "mode": "none", "content": exc.message, "citations": [], "meta": {"error": exc.code}, "latency_ms": 0}
    m = CopilotMessage(org_id=p.org_id, conversation_id=conv.id, role="assistant", content=res["content"], citations=res.get("citations", []),
                       status=res["status"] if res["status"] != "unavailable" else "blocked", mode=res["mode"], latency_ms=res.get("latency_ms"),
                       meta={**res.get("meta", {}), "reason": res.get("reason")})
    db.add(m)
    conv.updated_at = datetime.now(UTC)
    db.flush()
    if res["status"] in ("blocked", "refused"):
        _audit(p, request, f"copilot.{res['status']}", resource_type="copilot_conversation", resource_id=str(conv.id),
               details={"reason": res.get("reason")}, outcome="denied")
    return {"conversation_id": str(conv.id), "message": _msg(m), "disclaimer": DISCLAIMER}


@router.get("/conversations")
def conversations(p: Principal = Depends(require("copilot.use")), db: Session = Depends(tenant_db)) -> list[dict[str, Any]]:
    rows = db.execute(select(CopilotConversation).where(CopilotConversation.user_id == p.user_id)
                      .order_by(func.coalesce(CopilotConversation.updated_at, CopilotConversation.created_at).desc()).limit(50)).scalars()
    return [{"id": str(c.id), "title": c.title, "created_at": c.created_at, "updated_at": c.updated_at} for c in rows]


@router.get("/conversations/{conv_id}")
def conversation(conv_id: uuid.UUID, p: Principal = Depends(require("copilot.use")), db: Session = Depends(tenant_db)) -> dict[str, Any]:
    c = db.execute(select(CopilotConversation).where(CopilotConversation.id == conv_id, CopilotConversation.user_id == p.user_id)).scalar_one_or_none()
    if c is None:
        raise NotFound("Conversation not found")
    msgs = db.execute(select(CopilotMessage).where(CopilotMessage.conversation_id == c.id).order_by(CopilotMessage.created_at)).scalars()
    return {"id": str(c.id), "title": c.title, "messages": [_msg(m) for m in msgs], "disclaimer": DISCLAIMER}


@router.delete("/conversations/{conv_id}", status_code=204)
def delete_conversation(conv_id: uuid.UUID, p: Principal = Depends(require("copilot.use")), db: Session = Depends(tenant_db)) -> None:
    n = db.execute(delete(CopilotConversation).where(CopilotConversation.id == conv_id, CopilotConversation.user_id == p.user_id)).rowcount
    if not n:
        raise NotFound("Conversation not found")


class CalcIn(BaseModel):
    expression: str = Field(min_length=1, max_length=300)


@router.post("/calculate")
def calculate(body: CalcIn, p: Principal = Depends(require("copilot.use"))) -> dict[str, Any]:
    try:
        v = calculator.evaluate(body.expression)
    except calculator.CalcError as exc:
        raise ValidationFailed(str(exc), code="invalid_expression") from exc
    return {"expression": body.expression, "result": v}


@router.get("/sources/{scope}/{chunk_id}")
def source(scope: Literal["public", "private"], chunk_id: uuid.UUID, p: Principal = Depends(require("copilot.use")),
           cdb: Session = Depends(control_db), db: Session = Depends(tenant_db)) -> dict[str, Any]:
    """The full page/document around a citation, so the analyst can verify it in context."""
    if scope == "public":
        c = cdb.get(PublicChunk, chunk_id)
        if c is None:
            raise NotFound("Source not found")
        d = cdb.get(PublicDocument, c.document_id)
        assert d is not None
        chunks = cdb.execute(select(PublicChunk).where(PublicChunk.document_id == d.id).order_by(PublicChunk.ordinal)).scalars().all()
        return {"scope": "public", "title": d.title, "company": d.company, "fiscal_year": d.fiscal_year, "page": d.page, "doc_ref": d.doc_ref,
                "license": "FinQA (MIT) — extraits de rapports 10-K publics", "highlight": str(c.id),
                "chunks": [{"id": str(x.id), "kind": x.kind, "content": x.content} for x in chunks]}
    kc = db.execute(select(KbChunk).where(KbChunk.id == chunk_id, KbChunk.org_id == p.org_id)).scalar_one_or_none()
    if kc is None:
        raise NotFound("Source not found")
    doc = db.get(Document, kc.document_id)
    assert doc is not None
    chunks = db.execute(select(KbChunk).where(KbChunk.document_id == doc.id).order_by(KbChunk.ordinal)).scalars().all()
    return {"scope": "private", "title": doc.filename, "highlight": str(kc.id), "is_demo": doc.is_demo,
            "chunks": [{"id": str(x.id), "kind": x.kind, "content": x.content} for x in chunks]}


# ------------------------------------------------------------------------------------------------
# Private knowledge base
# ------------------------------------------------------------------------------------------------
@router.get("/kb")
def kb_list(p: Principal = Depends(require("copilot.use")), db: Session = Depends(tenant_db)) -> list[dict[str, Any]]:
    rows = db.execute(select(Document, func.count(KbChunk.id)).outerjoin(KbChunk, KbChunk.document_id == Document.id)
                      .where(Document.doc_type == "kb").group_by(Document.id).order_by(Document.created_at.desc())).all()
    return [{"id": str(d.id), "filename": d.filename, "size_bytes": d.size_bytes, "chunks": n, "created_at": d.created_at, "is_demo": d.is_demo}
            for d, n in rows]


@router.post("/kb", status_code=201)
async def kb_upload(request: Request, file: UploadFile = File(...), p: Principal = Depends(require("data.manage")),
                    db: Session = Depends(tenant_db)) -> dict[str, Any]:
    max_bytes = get_settings().max_upload_bytes
    data = await file.read(max_bytes + 1)
    name = file.filename or "document.txt"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if len(data) > max_bytes or not data:
        raise ValidationFailed("Empty or too large file")
    if ext in ("txt", "md"):
        try:
            body = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationFailed("Text files must be UTF-8", code="encoding") from exc
        if "\x00" in body:
            raise ValidationFailed("Binary content in a text file", code="unsupported_type")
        mime, pages = "text/plain", [(1, body)]
    else:
        mime = process.validate_upload(data, name, max_bytes)
        if mime != "application/pdf":
            raise ValidationFailed("Knowledge base accepts PDF, TXT or Markdown", code="unsupported_type")
        pages = [(pg.no, pg.text) for pg in process.extract(data, mime)]
    sha = hashlib.sha256(data).hexdigest()
    if db.execute(select(Document.id).where(Document.sha256 == sha)).first():
        raise Conflict("This file is already in the organisation", code="duplicate_document")
    doc_id = uuid.uuid4()
    key = org_key(p.org_id, "kb", str(doc_id), name.replace("/", "_")[:120])
    get_store().put(key, data, mime)
    doc = Document(id=doc_id, org_id=p.org_id, filename=name[:255], mime_type=mime, size_bytes=len(data), sha256=sha, storage_key=key,
                   doc_type="kb", status="validated", page_count=len(pages), text="\n".join(t for _, t in pages)[:500_000],
                   uploaded_by=p.actor_id, is_demo=p.org_kind == "demo", engine="local")
    db.add(doc)
    db.flush()
    n = service.index_kb_document(db, doc, pages)
    _audit(p, request, "copilot.kb_uploaded", resource_type="document", resource_id=str(doc.id), details={"filename": name, "chunks": n})
    return {"id": str(doc.id), "filename": doc.filename, "chunks": n}


@router.delete("/kb/{doc_id}", status_code=204)
def kb_delete(doc_id: uuid.UUID, request: Request, p: Principal = Depends(require("data.manage")), db: Session = Depends(tenant_db)) -> None:
    d = db.execute(select(Document).where(Document.id == doc_id, Document.org_id == p.org_id, Document.doc_type == "kb")).scalar_one_or_none()
    if d is None:
        raise NotFound("Knowledge-base document not found")
    get_store().delete(d.storage_key)
    db.delete(d)
    _audit(p, request, "copilot.kb_deleted", resource_type="document", resource_id=str(doc_id), details={"filename": d.filename})
