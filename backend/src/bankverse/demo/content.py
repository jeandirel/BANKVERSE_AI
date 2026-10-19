"""DEMO content for document review and the copilot's private knowledge base."""

from __future__ import annotations

import hashlib
import random
import uuid
from typing import Any

from sqlalchemy import select

from bankverse.core.storage import get_store, org_key
from bankverse.db.models.control import Organization
from bankverse.db.models.tenant import Customer, Document
from bankverse.db.session import TenantTarget, control_session, tenant_session
from bankverse.demo.kb_texts import KB_DOCUMENTS
from bankverse.modules.documents import service as doc_service
from bankverse.modules.documents import synth

DEMO_IMPORT_ACTOR = uuid.UUID("00000000-0000-0000-0000-0000000d0c5e")  # "DEMO automatic import" (not a user)


def seed_content(org_slug: str = "demo-bank", n_specimens: int = 24, seed: int = 2026) -> dict[str, Any]:
    with control_session() as db:
        org = db.execute(select(Organization).where(Organization.slug == org_slug)).scalar_one()
        target = TenantTarget(org.id, org.isolation_mode, org.dedicated_db_secret)
        org_id = org.id
    rng = random.Random(seed)  # nosec B311
    created, skipped = 0, 0
    with tenant_session(target) as db:
        customers = [c for (c,) in db.execute(select(Customer.id).where(Customer.is_demo.is_(True)).limit(400)).all()]
        for i in range(n_specimens):
            t = synth.DOC_TYPES[i % len(synth.DOC_TYPES)]
            s = seed * 1000 + i
            d = synth.generate(t, tpl=rng.randint(0, 2), seed=s, defect_rate=0.3)
            scanned = i % 4 == 3
            data, name = (synth.to_scan(d.pdf, seed=s), f"DEMO_{t}_{s}_scan.png") if scanned else (d.pdf, f"DEMO_{t}_{s}.pdf")
            sha = hashlib.sha256(data).hexdigest()
            if db.execute(select(Document.id).where(Document.sha256 == sha)).first():
                skipped += 1
                continue
            doc_service.ingest(db, org_id=org_id, actor_id=DEMO_IMPORT_ACTOR, data=data, filename=name,
                               customer_id=rng.choice(customers) if customers else None, is_demo=True)
            created += 1
    kb = seed_kb(target, org_id)
    return {"org": org_slug, "specimens_created": created, "specimens_skipped": skipped, **kb}


def seed_kb(target: TenantTarget, org_id: uuid.UUID) -> dict[str, Any]:
    from bankverse.modules.copilot import service as copilot

    created, chunks = 0, 0
    with tenant_session(target) as db:
        for filename, body in KB_DOCUMENTS.items():
            data = body.encode("utf-8")
            sha = hashlib.sha256(data).hexdigest()
            if db.execute(select(Document.id).where(Document.sha256 == sha)).first():
                continue
            doc_id = uuid.uuid4()
            key = org_key(org_id, "kb", str(doc_id), filename)
            get_store().put(key, data, "text/plain")
            doc = Document(id=doc_id, org_id=org_id, filename=filename, mime_type="text/plain", size_bytes=len(data), sha256=sha,
                           storage_key=key, doc_type="kb", status="validated", page_count=1, text=body, uploaded_by=DEMO_IMPORT_ACTOR,
                           is_demo=True, engine="local")
            db.add(doc)
            db.flush()
            chunks += copilot.index_kb_document(db, doc, [(1, body)])
            created += 1
    return {"kb_documents_created": created, "kb_chunks": chunks}
