"""Global search across tenant entities (RLS-scoped; results filtered by the caller's permissions)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from bankverse.auth.deps import Principal, get_principal, tenant_db
from bankverse.db.models.tenant import Account, AmlAlert, Case, CreditApplication, Customer, Document

router = APIRouter(tags=["search"])


@router.get("/search")
def search(q: str = Query(min_length=2, max_length=100), p: Principal = Depends(get_principal),
           db: Session = Depends(tenant_db)) -> dict[str, Any]:
    like = f"%{q}%"
    hits: list[dict[str, Any]] = []
    if p.can("customer.read") or p.can("fraud.read") or p.can("aml.read"):
        for c in db.execute(select(Customer).where(or_(Customer.full_name.ilike(like), Customer.external_ref.ilike(like)))
                            .limit(6)).scalars():
            href = f"/app/customers/{c.id}" if p.can("customer.read") else "/app/fraud"
            hits.append({"type": "customer", "id": str(c.id), "title": c.full_name,
                         "subtitle": f"{c.external_ref} · {c.city or ''} · {c.segment}", "href": href})
    if p.can("cases.read"):
        for c in db.execute(select(Case).where(or_(Case.reference.ilike(like), Case.title.ilike(like))).limit(5)).scalars():
            hits.append({"type": "case", "id": str(c.id), "title": f"{c.reference} — {c.title}", "subtitle": c.status,
                         "href": f"/app/cases/{c.id}"})
    if p.can("aml.read"):
        rows = db.execute(select(AmlAlert, Account).join(Account, Account.id == AmlAlert.account_id)
                          .where(Account.iban.ilike(f"%{q.replace(' ', '')}%")).limit(4)).all()
        for a, acc in rows:
            hits.append({"type": "aml_alert", "id": str(a.id), "title": acc.iban, "subtitle": ", ".join(a.typologies),
                         "href": f"/app/aml/alerts/{a.id}"})
    if p.can("documents.read"):
        for d in db.execute(select(Document).where(Document.filename.ilike(like)).limit(4)).scalars():
            hits.append({"type": "document", "id": str(d.id), "title": d.filename, "subtitle": d.doc_type or d.status,
                         "href": f"/app/documents/{d.id}"})
    if p.can("credit.read"):
        for a in db.execute(select(CreditApplication).where(CreditApplication.reference.ilike(like)).limit(4)).scalars():
            hits.append({"type": "application", "id": str(a.id), "title": a.reference, "subtitle": a.status,
                         "href": f"/app/credit/applications/{a.id}"})
    return {"hits": hits}
