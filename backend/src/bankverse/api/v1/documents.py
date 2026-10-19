"""Document AI / KYC-KYB API. Assistance to human review only — never an identity verification."""

from __future__ import annotations

import random
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from bankverse.auth.deps import Principal, client_ip, require, tenant_db
from bankverse.core.config import get_settings
from bankverse.core.errors import Conflict, Forbidden, NotFound, Unavailable, ValidationFailed
from bankverse.core.storage import assert_key_in_org, get_store
from bankverse.db.models.tenant import (
    Case,
    CaseEvent,
    Customer,
    Document,
    DocumentCheck,
    DocumentField,
    DocumentPage,
)
from bankverse.modules.documents import process, service, synth
from bankverse.services import audit, usage
from bankverse.services.cases import new_case_reference

router = APIRouter(prefix="/documents", tags=["documents"])
DISCLAIMER = ("Aide à la revue documentaire : extraction et contrôles automatiques à valider par un analyste. "
              "Ce n'est ni une vérification d'identité réglementaire ni une certification d'authenticité.")
STATUSES = ("processing", "needs_review", "validated", "rejected", "failed")
DEMO_MARKERS = ("specimen", "spécimen", "fictif", "demo", "démo")
NOT_KB = or_(Document.doc_type.is_(None), Document.doc_type != "kb")  # knowledge-base files belong to the copilot


def _audit(p: Principal, request: Request, action: str, **kw: Any) -> None:
    audit.record(action, org_id=p.org_id, actor_user_id=p.user_id, actor_type=p.kind, actor_label=p.label, ip=client_ip(request), **kw)


def _doc(db: Session, p: Principal, doc_id: uuid.UUID) -> Document:
    d = db.execute(select(Document).where(Document.id == doc_id, Document.org_id == p.org_id, NOT_KB)).scalar_one_or_none()
    if d is None:
        raise NotFound("Document not found")
    return d


def _row(d: Document, checks: dict[uuid.UUID, dict[str, int]] | None = None) -> dict[str, Any]:
    c = (checks or {}).get(d.id, {})
    return {"id": str(d.id), "filename": d.filename, "mime_type": d.mime_type, "size_bytes": d.size_bytes, "doc_type": d.doc_type,
            "doc_type_label": synth.DOC_LABELS.get(d.doc_type or "", None), "confidence": d.doc_type_confidence, "status": d.status,
            "page_count": d.page_count, "ocr_used": d.ocr_used, "created_at": d.created_at, "is_demo": d.is_demo,
            "customer_id": str(d.customer_id) if d.customer_id else None, "checks_failed": c.get("fail", 0), "checks_warn": c.get("warn", 0),
            "processing_ms": d.processing_ms}


def _check_counts(db: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, int]]:
    out: dict[uuid.UUID, dict[str, int]] = {}
    if not ids:
        return out
    for did, st, n in db.execute(select(DocumentCheck.document_id, DocumentCheck.status, func.count())
                                 .where(DocumentCheck.document_id.in_(ids)).group_by(DocumentCheck.document_id, DocumentCheck.status)):
        out.setdefault(did, {})[st] = n
    return out


@router.get("/overview")
def overview(p: Principal = Depends(require("documents.read")), db: Session = Depends(tenant_db)) -> dict[str, Any]:
    by_status = dict(db.execute(select(Document.status, func.count()).where(NOT_KB).group_by(Document.status)).all())
    by_type = dict(db.execute(select(func.coalesce(Document.doc_type, "unknown"), func.count()).where(NOT_KB).group_by(Document.doc_type)).all())
    failing = db.execute(select(DocumentCheck.check_name, func.count()).where(DocumentCheck.status == "fail")
                         .group_by(DocumentCheck.check_name).order_by(func.count().desc())).all()
    avg_ms = db.execute(select(func.avg(Document.processing_ms)).where(NOT_KB)).scalar_one()
    ocr = db.execute(select(func.count()).where(Document.ocr_used.is_(True), NOT_KB)).scalar_one()
    clf, threshold, tag = service.classifier()
    return {"by_status": by_status, "by_type": by_type, "failing_checks": [{"check": k, "count": n} for k, n in failing],
            "avg_processing_ms": round(float(avg_ms), 0) if avg_ms else None, "ocr_documents": ocr,
            "classifier": {"deployed": clf is not None, "model": tag, "reject_threshold": threshold if clf is not None else None},
            "doc_types": [{"key": k, "label": v} for k, v in synth.DOC_LABELS.items()], "disclaimer": DISCLAIMER}


@router.get("")
def list_documents(
    p: Principal = Depends(require("documents.read")),
    db: Session = Depends(tenant_db),
    status: str | None = Query(default=None),
    doc_type: str | None = Query(default=None, max_length=48),
    q: str | None = Query(default=None, max_length=120),
    customer_id: uuid.UUID | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    query = select(Document).where(NOT_KB)
    if status:
        sts = status.split(",")
        if not set(sts) <= set(STATUSES):
            raise ValidationFailed("Unknown status")
        query = query.where(Document.status.in_(sts))
    if doc_type:
        query = query.where(Document.doc_type.is_(None)) if doc_type == "unknown" else query.where(Document.doc_type == doc_type)
    if customer_id:
        query = query.where(Document.customer_id == customer_id)
    if q:
        query = query.where(Document.tsv.op("@@")(func.websearch_to_tsquery("simple", q)) | Document.filename.ilike(f"%{q}%"))
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    docs = list(db.execute(query.order_by(Document.created_at.desc()).limit(page_size).offset((page - 1) * page_size)).scalars())
    counts = _check_counts(db, [d.id for d in docs])
    return {"total": total, "page": page, "page_size": page_size, "items": [_row(d, counts) for d in docs], "disclaimer": DISCLAIMER}


@router.get("/search")
def search(q: str = Query(min_length=2, max_length=120), p: Principal = Depends(require("documents.read")),
           db: Session = Depends(tenant_db)) -> list[dict[str, Any]]:
    tsq = func.websearch_to_tsquery("simple", q)
    rows = db.execute(
        select(Document, func.ts_rank(Document.tsv, tsq).label("rank"),
               func.ts_headline("simple", func.coalesce(Document.text, ""), tsq,
                                text("'MaxFragments=2, MinWords=4, MaxWords=14, StartSel=<<, StopSel=>>'")).label("snippet"))
        .where(Document.tsv.op("@@")(tsq), NOT_KB).order_by(text("rank DESC")).limit(30)).all()
    return [{**_row(d), "rank": round(float(r), 4), "snippet": s} for d, r, s in rows]


def _consume_pages(p: Principal, n: int) -> None:
    usage.consume(p.org_id, "document_pages", max(n, 1))


def _upload_common(p: Principal, request: Request, db: Session, data: bytes, filename: str, customer_id: uuid.UUID | None,
                   is_demo: bool) -> dict[str, Any]:
    if customer_id is not None and db.execute(select(Customer.id).where(Customer.id == customer_id, Customer.org_id == p.org_id)).first() is None:
        raise NotFound("Customer not found")
    _consume_pages(p, 1)
    doc = service.ingest(db, org_id=p.org_id, actor_id=p.actor_id, data=data, filename=filename, customer_id=customer_id, is_demo=is_demo)
    if p.org_kind == "demo" and not is_demo and not any(m in (doc.text or "").casefold() for m in DEMO_MARKERS):
        # The public demonstration must never store real identity or financial documents.
        store = get_store()
        store.delete(doc.storage_key)
        db.delete(doc)
        db.flush()
        _audit(p, request, "document.refused_demo", outcome="denied", details={"filename": filename})
        raise ValidationFailed("Demo environment: only DEMO specimens may be uploaded (no real documents).",
                               code="demo_real_document_refused")
    if doc.page_count > 1:
        _consume_pages(p, doc.page_count - 1)
    _audit(p, request, "document.uploaded", resource_type="document", resource_id=str(doc.id),
           details={"filename": doc.filename, "doc_type": doc.doc_type, "pages": doc.page_count, "demo": is_demo})
    return detail_payload(db, doc)


@router.post("", status_code=201)
async def upload(request: Request, file: UploadFile = File(...), customer_id: uuid.UUID | None = Form(default=None),
                 p: Principal = Depends(require("documents.upload")), db: Session = Depends(tenant_db)) -> dict[str, Any]:
    max_bytes = get_settings().max_upload_bytes
    data = await file.read(max_bytes + 1)
    return _upload_common(p, request, db, data, file.filename or "document", customer_id, is_demo=False)


class SpecimenIn(BaseModel):
    doc_type: Literal["bank_statement", "invoice", "payslip", "company_extract", "utility_bill"]
    scanned: bool = False
    with_anomaly: bool = False
    customer_id: uuid.UUID | None = None


@router.post("/specimens", status_code=201)
def specimen(body: SpecimenIn, request: Request, p: Principal = Depends(require("documents.upload")),
             db: Session = Depends(tenant_db)) -> dict[str, Any]:
    """Generates a synthetic DEMO specimen (watermarked, fictitious) and processes it like an upload."""
    seed = random.SystemRandom().randint(1, 10**9)
    d = synth.generate(body.doc_type, tpl=seed % 3, seed=seed, defect_rate=1.0 if body.with_anomaly else 0.0)
    if body.scanned:
        data, name = synth.to_scan(d.pdf, seed=seed), f"DEMO_{body.doc_type}_{seed}_scan.png"
    else:
        data, name = d.pdf, f"DEMO_{body.doc_type}_{seed}.pdf"
    out = _upload_common(p, request, db, data, name, body.customer_id, is_demo=True)
    out["specimen"] = {"layout": d.template, "injected_anomalies": d.defects}
    return out


def detail_payload(db: Session, d: Document) -> dict[str, Any]:
    pages = list(db.execute(select(DocumentPage).where(DocumentPage.document_id == d.id).order_by(DocumentPage.page_no)).scalars())
    fields = list(db.execute(select(DocumentField).where(DocumentField.document_id == d.id).order_by(DocumentField.created_at)).scalars())
    checks = list(db.execute(select(DocumentCheck).where(DocumentCheck.document_id == d.id).order_by(DocumentCheck.created_at)).scalars())
    customer = db.get(Customer, d.customer_id) if d.customer_id else None
    return {
        **_row(d), "error": d.error, "validated_by": str(d.validated_by) if d.validated_by else None, "validated_at": d.validated_at,
        "uploaded_by": str(d.uploaded_by), "sha256": d.sha256, "engine": d.engine,
        "customer": {"id": str(customer.id), "name": customer.full_name, "ref": customer.external_ref} if customer else None,
        "pages": [{"page_no": pg.page_no, "width": pg.width, "height": pg.height, "ocr_used": pg.ocr_used,
                   "ocr_confidence": round(pg.ocr_confidence, 1) if pg.ocr_confidence is not None else None,
                   "word_count": len(pg.words), "tables": pg.tables[:3], "text": pg.text[:20_000]} for pg in pages],
        "fields": [{"id": str(f.id), "name": f.name, "value": f.value, "normalized": f.normalized, "confidence": round(f.confidence, 3),
                    "page_no": f.page_no, "bbox": f.bbox, "required": f.required, "source": f.source,
                    "corrected_at": f.corrected_at} for f in fields],
        "checks": [{"name": c.check_name, "status": c.status, "detail": c.detail} for c in checks],
        "doc_types": [{"key": k, "label": v} for k, v in synth.DOC_LABELS.items()],
        "disclaimer": DISCLAIMER,
    }


@router.get("/{doc_id}")
def get_document(doc_id: uuid.UUID, p: Principal = Depends(require("documents.read")), db: Session = Depends(tenant_db)) -> dict[str, Any]:
    return detail_payload(db, _doc(db, p, doc_id))


@router.get("/{doc_id}/pages/{page_no}/image")
def page_image(doc_id: uuid.UUID, page_no: int, p: Principal = Depends(require("documents.read")),
               db: Session = Depends(tenant_db)) -> Response:
    d = _doc(db, p, doc_id)
    try:
        png = service.render_page(d, page_no)
    except IndexError:
        raise NotFound("Page not found") from None
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=600"})


@router.get("/{doc_id}/file")
def original(doc_id: uuid.UUID, request: Request, p: Principal = Depends(require("documents.read")),
             db: Session = Depends(tenant_db)) -> Response:
    d = _doc(db, p, doc_id)
    assert_key_in_org(d.storage_key, p.org_id)
    data = get_store().get(d.storage_key)
    _audit(p, request, "document.downloaded", resource_type="document", resource_id=str(d.id))
    return Response(data, media_type=d.mime_type, headers={"Content-Disposition": f'attachment; filename="{d.filename}"',
                                                            "X-Content-Type-Options": "nosniff"})


class FieldCorrection(BaseModel):
    value: str = Field(min_length=1, max_length=500)


@router.patch("/{doc_id}/fields/{field_id}")
def correct_field(doc_id: uuid.UUID, field_id: uuid.UUID, body: FieldCorrection, request: Request,
                  p: Principal = Depends(require("documents.validate")), db: Session = Depends(tenant_db)) -> dict[str, Any]:
    d = _doc(db, p, doc_id)
    if d.status in ("validated", "rejected"):
        raise Conflict("Document already reviewed; reopen it by re-processing")
    f = db.execute(select(DocumentField).where(DocumentField.id == field_id, DocumentField.document_id == d.id)).scalar_one_or_none()
    if f is None:
        raise NotFound("Field not found")
    normalized = service.normalize_value(f.name, body.value)
    if normalized is None:
        raise ValidationFailed(f"Value is not a valid {f.name}")
    before = f.normalized
    f.value, f.normalized, f.source, f.confidence = body.value, normalized, "corrected", 1.0
    f.corrected_by = p.user_id
    f.corrected_at = func.now()
    db.flush()
    service.recheck(db, d)
    _audit(p, request, "document.field_corrected", resource_type="document", resource_id=str(d.id),
           details={"field": f.name, "before": before, "after": normalized})
    return detail_payload(db, d)


class TypeIn(BaseModel):
    doc_type: Literal["bank_statement", "invoice", "payslip", "company_extract", "utility_bill"]


@router.post("/{doc_id}/reprocess")
def reprocess(doc_id: uuid.UUID, request: Request, body: TypeIn | None = None, p: Principal = Depends(require("documents.upload")),
              db: Session = Depends(tenant_db)) -> dict[str, Any]:
    d = _doc(db, p, doc_id)
    _consume_pages(p, d.page_count)
    service.run(db, d, forced_type=body.doc_type if body else None)
    _audit(p, request, "document.reprocessed", resource_type="document", resource_id=str(d.id),
           details={"forced_type": body.doc_type if body else None})
    return detail_payload(db, d)


class ReviewIn(BaseModel):
    decision: Literal["validated", "rejected"]
    comment: str = Field(min_length=3, max_length=2000)


@router.post("/{doc_id}/review")
def review(doc_id: uuid.UUID, body: ReviewIn, request: Request, p: Principal = Depends(require("documents.validate")),
           db: Session = Depends(tenant_db)) -> dict[str, Any]:
    d = _doc(db, p, doc_id)
    if d.status not in ("needs_review",):
        raise Conflict(f"Document is {d.status}")
    if body.decision == "validated":
        if d.uploaded_by == p.actor_id:
            raise Forbidden("Four-eyes principle: the uploader cannot validate the same document", code="four_eyes")
        if d.doc_type is None:
            raise ValidationFailed("Set the document type before validating")
        missing = db.execute(select(DocumentField.name).where(DocumentField.document_id == d.id, DocumentField.required.is_(True),
                                                               DocumentField.normalized.is_(None))).scalars().all()
        if missing:
            raise ValidationFailed("Required fields are missing; correct them first", details={"fields": list(missing)})
    assert p.user_id is not None
    service.mark(d, body.decision, p.user_id)
    _audit(p, request, f"document.{body.decision}", resource_type="document", resource_id=str(d.id), details={"comment": body.comment})
    return detail_payload(db, d)


@router.post("/{doc_id}/case")
def to_case(doc_id: uuid.UUID, request: Request, p: Principal = Depends(require("cases.write")),
            db: Session = Depends(tenant_db)) -> dict[str, Any]:
    if not p.can("documents.read"):
        raise NotFound("Document not found")
    d = _doc(db, p, doc_id)
    fails = db.execute(select(DocumentCheck).where(DocumentCheck.document_id == d.id, DocumentCheck.status == "fail")).scalars().all()
    case_obj = Case(org_id=p.org_id, reference=new_case_reference(db, "KY"), case_type="kyc",
                    title=f"Revue documentaire — {synth.DOC_LABELS.get(d.doc_type or '', 'document')} {d.filename}",
                    status="investigating", priority="high" if fails else "medium", customer_id=d.customer_id,
                    created_by=p.actor_id, assigned_to=p.user_id)
    db.add(case_obj)
    db.flush()
    db.add(CaseEvent(org_id=p.org_id, case_id=case_obj.id, actor_id=p.user_id, actor_label=p.label, kind="evidence",
                     payload={"text": f"Document {d.filename} ({d.sha256[:12]}…). Contrôles en échec : "
                                      + ("; ".join(f"{c.check_name} — {c.detail}" for c in fails) or "aucun"),
                              "document_id": str(d.id)}))
    _audit(p, request, "case.created", resource_type="case", resource_id=str(case_obj.id), details={"document": str(d.id)})
    return {"case_id": str(case_obj.id), "reference": case_obj.reference}


@router.post("/{doc_id}/analyze")
def analyze(doc_id: uuid.UUID, p: Principal = Depends(require("documents.read")), db: Session = Depends(tenant_db)) -> dict[str, Any]:
    _doc(db, p, doc_id)
    if get_settings().llm_provider == "none":
        raise Unavailable("Generative analysis requires an LLM provider (Amazon Bedrock or Anthropic API) configured for this "
                          "environment. Deterministic extraction and controls remain available.", code="llm_unavailable")
    raise Unavailable("LLM document analysis is not enabled for this organisation", code="llm_unavailable")


__all__ = ["process", "router"]
