"""Document AI service layer: ingestion, persistence, re-processing and page rendering."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import cv2
import numpy as np
import pymupdf as fitz
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from bankverse.core.config import get_settings
from bankverse.core.errors import AppError, Conflict
from bankverse.core.storage import assert_key_in_org, get_store, org_key
from bankverse.db.models.tenant import Document, DocumentCheck, DocumentField, DocumentPage
from bankverse.ml import registry
from bankverse.modules.documents import process

MODEL_NAME = "document_classifier"
RENDER_DPI = 110


def classifier() -> tuple[Any | None, float, str | None]:
    """Returns (sklearn pipeline, reject threshold, model tag) or (None, 1.0, None) if no deployed model."""
    try:
        m = registry.load_production(MODEL_NAME)
    except AppError:
        return None, 1.0, None
    return m.payload["classifier"], float(m.payload.get("reject_threshold", 0.55)), m.tag


def _safe_name(filename: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", filename.rsplit("/", 1)[-1])[:120] or "document"
    return base


def ingest(db: Session, *, org_id: uuid.UUID, actor_id: uuid.UUID, data: bytes, filename: str,
           customer_id: uuid.UUID | None = None, is_demo: bool = False) -> Document:
    settings = get_settings()
    mime = process.validate_upload(data, filename, settings.max_upload_bytes)
    sha = hashlib.sha256(data).hexdigest()
    existing = db.execute(select(Document).where(Document.org_id == org_id, Document.sha256 == sha)).scalar_one_or_none()
    if existing is not None:
        raise Conflict("This file has already been uploaded", code="duplicate_document", details={"document_id": str(existing.id)})
    doc_id = uuid.uuid4()
    key = org_key(org_id, "documents", str(doc_id), _safe_name(filename))
    get_store().put(key, data, mime)
    doc = Document(id=doc_id, org_id=org_id, filename=_safe_name(filename), mime_type=mime, size_bytes=len(data), sha256=sha,
                   storage_key=key, customer_id=customer_id, status="processing", uploaded_by=actor_id, is_demo=is_demo)
    db.add(doc)
    db.flush()
    run(db, doc, data)
    return doc


def run(db: Session, doc: Document, data: bytes | None = None, forced_type: str | None = None) -> None:
    """(Re)process a document. Fields corrected by a human are preserved on re-processing."""
    if data is None:
        assert_key_in_org(doc.storage_key, doc.org_id)
        data = get_store().get(doc.storage_key)
    clf, threshold, tag = classifier()
    try:
        res = process.process(data, doc.mime_type, clf)
    except Exception as exc:  # corrupted content discovered while rendering / OCR
        doc.status, doc.error = "failed", f"{type(exc).__name__}: processing failed"
        return
    suggestion = res["doc_type"]
    conf = res["confidence"]
    doc_type = forced_type or (suggestion if conf is not None and conf >= threshold else None)
    if doc_type and doc_type != suggestion:
        res["fields"] = process.extract_fields(doc_type, res["pages"])
    corrected = {f.name: f for f in db.execute(select(DocumentField).where(DocumentField.document_id == doc.id,
                                                                           DocumentField.source == "corrected")).scalars()}
    db.execute(delete(DocumentPage).where(DocumentPage.document_id == doc.id))
    db.execute(delete(DocumentField).where(DocumentField.document_id == doc.id, DocumentField.source != "corrected"))
    db.execute(delete(DocumentCheck).where(DocumentCheck.document_id == doc.id))
    for p in res["pages"]:
        db.add(DocumentPage(org_id=doc.org_id, document_id=doc.id, page_no=p.no, width=p.width, height=p.height, ocr_used=p.ocr,
                            ocr_confidence=p.ocr_conf, text=p.text[:200_000],
                            words=[{"t": w.text, "b": [round(v, 1) for v in w.bbox]} for w in p.words[:5000]], tables=p.tables))
    fields = res["fields"] if doc_type else []
    for f in fields:
        if f["name"] in corrected:
            continue
        db.add(DocumentField(org_id=doc.org_id, document_id=doc.id, name=f["name"], value=f["value"], normalized=f["normalized"],
                             confidence=f["confidence"], page_no=f["page_no"], bbox=f["bbox"], required=f["required"]))
    merged = [({**f, **_field_dict(corrected[f["name"]])} if f["name"] in corrected else f) for f in fields]
    checks = process.run_checks(doc_type, merged) if doc_type else []
    if not doc_type:
        if clf is None:
            checks.append({"check_name": "classification", "status": "warn",
                           "detail": "Aucun modèle de classification déployé : choisissez le type de document manuellement."})
        else:
            checks.append({"check_name": "classification", "status": "warn",
                           "detail": f"Type non reconnu avec assez de confiance (suggestion : {suggestion}, {conf or 0:.0%}). "
                                     "Choisissez le type manuellement."})
    for c in checks:
        db.add(DocumentCheck(org_id=doc.org_id, document_id=doc.id, **c))
    doc.doc_type = doc_type
    doc.doc_type_confidence = conf if not forced_type else 1.0
    doc.page_count = len(res["pages"])
    doc.ocr_used = res["ocr"]
    doc.text = res["text"][:500_000]
    doc.processing_ms = res["processing_ms"]
    doc.engine = "local"
    doc.error = None
    doc.status = "needs_review"
    doc.validated_by = None
    doc.validated_at = None
    db.flush()
    _ = tag


def _field_dict(f: DocumentField) -> dict[str, Any]:
    return {"name": f.name, "value": f.value, "normalized": f.normalized, "confidence": f.confidence, "page_no": f.page_no,
            "bbox": f.bbox, "required": f.required}


def recheck(db: Session, doc: Document) -> list[dict[str, str]]:
    fields = [_field_dict(f) for f in db.execute(select(DocumentField).where(DocumentField.document_id == doc.id)).scalars()]
    db.execute(delete(DocumentCheck).where(DocumentCheck.document_id == doc.id))
    checks = process.run_checks(doc.doc_type, fields) if doc.doc_type else []
    for c in checks:
        db.add(DocumentCheck(org_id=doc.org_id, document_id=doc.id, **c))
    db.flush()
    return checks


def normalize_value(name: str, value: str) -> str | None:
    if name == "iban":
        return re.sub(r"\s", "", value).upper()
    if name == "period":
        m = re.fullmatch(r"(\d{2})/(\d{4})", value.strip()) or re.fullmatch(r"(\d{4})-(\d{2})", value.strip())
        if not m:
            return None
        return f"{m.group(2)}-{m.group(1)}" if "/" in value else value.strip()
    if any(k in name for k in ("balance", "total", "vat", "salary", "net_", "amount", "capital")) and name != "vat_rate":
        a = process.parse_amount(value)
        return None if a is None else f"{a:.2f}"
    if "date" in name or name.startswith("period_"):
        d = process.parse_date(value) if "/" in value else None
        if d:
            return d.isoformat()
        try:
            return datetime.fromisoformat(value.strip()).date().isoformat()
        except ValueError:
            return None
    return value.strip() or None


def render_page(doc: Document, page_no: int) -> bytes:
    """PNG rendering of one page (cached in object storage next to the original)."""
    store = get_store()
    key = org_key(doc.org_id, "documents", str(doc.id), f"page-{page_no}.png")
    if store.exists(key):
        return store.get(key)
    assert_key_in_org(doc.storage_key, doc.org_id)
    data = store.get(doc.storage_key)
    if doc.mime_type == "application/pdf":
        with fitz.open(stream=data, filetype="pdf") as d:
            if page_no < 1 or page_no > d.page_count:
                raise IndexError(page_no)
            png = d[page_no - 1].get_pixmap(dpi=RENDER_DPI).tobytes("png")
    else:
        if page_no != 1:
            raise IndexError(page_no)
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        h, w = img.shape[:2]
        target_w = int(595 * RENDER_DPI / 72)
        img = cv2.resize(img, (target_w, int(h * target_w / w)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".png", img)
        assert ok
        png = buf.tobytes()
    store.put(key, png, "image/png")
    return png


def mark(doc: Document, decision: str, user_id: uuid.UUID) -> None:
    doc.status = decision
    doc.validated_by = user_id
    doc.validated_at = datetime.now(UTC)
