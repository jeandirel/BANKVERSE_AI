"""Document AI API: secure upload, extraction, controls, human review (four-eyes) and tenant isolation."""

from __future__ import annotations

import io

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from bankverse.modules.documents import synth
from tests.conftest import login

pytestmark = pytest.mark.integration


def _upload(client, headers, data: bytes, name: str, mime: str = "application/pdf"):
    return client.post("/api/v1/documents", files={"file": (name, data, mime)}, headers=headers)


def _plain_pdf(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawString(72, 750, text)
    c.save()
    return buf.getvalue()


def test_upload_extract_force_type_and_controls(client, tenants):
    org = tenants.org()
    analyst = tenants.user(org, "credit_analyst")
    h = login(client, analyst)
    doc = synth.generate("invoice", 0, seed=101, defect_rate=0)
    r = _upload(client, h, doc.pdf, "facture.pdf")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "needs_review" and body["page_count"] == 1 and body["ocr_used"] is False
    if body["doc_type"] is None:  # no deployed classifier in the test database → explicit, not faked
        assert any(c["name"] == "classification" and c["status"] == "warn" for c in body["checks"])
        r = client.post(f"/api/v1/documents/{body['id']}/reprocess", json={"doc_type": "invoice"}, headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
    assert body["doc_type"] == "invoice"
    fields = {f["name"]: f for f in body["fields"]}
    assert float(fields["total_ttc"]["normalized"]) == pytest.approx(doc.truth["total_ttc"])
    assert fields["siren"]["normalized"] == doc.truth["siren"]
    assert fields["total_ttc"]["bbox"] is not None and fields["total_ttc"]["page_no"] == 1
    statuses = {c["name"]: c["status"] for c in body["checks"]}
    assert statuses["invoice_totals"] == "pass" and statuses["siren_luhn"] == "pass" and statuses["required_fields"] == "pass"
    # page rendering and original download
    img = client.get(f"/api/v1/documents/{body['id']}/pages/1/image")
    assert img.status_code == 200 and img.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.get(f"/api/v1/documents/{body['id']}/pages/9/image").status_code == 404
    # duplicate detection
    dup = _upload(client, h, doc.pdf, "copie.pdf")
    assert dup.status_code == 409 and dup.json()["error"]["details"]["document_id"] == body["id"]


def test_anomaly_detected_then_corrected_field_rechecks(client, tenants):
    org = tenants.org()
    h = login(client, tenants.user(org, "credit_analyst"))
    doc = synth.generate("invoice", 1, seed=7, defect_rate=1.0)
    assert doc.defects == ["totals_mismatch"]
    body = _upload(client, h, doc.pdf, "f.pdf").json()
    body = client.post(f"/api/v1/documents/{body['id']}/reprocess", json={"doc_type": "invoice"}, headers=h).json()
    assert {c["name"]: c["status"] for c in body["checks"]}["invoice_totals"] == "fail"
    f = next(x for x in body["fields"] if x["name"] == "total_ttc")
    fixed = round(float(next(x for x in body["fields"] if x["name"] == "total_ht")["normalized"])
                  + float(next(x for x in body["fields"] if x["name"] == "vat")["normalized"]), 2)
    r = client.patch(f"/api/v1/documents/{body['id']}/fields/{f['id']}", json={"value": f"{fixed:.2f}".replace(".", ",")}, headers=h)
    assert r.status_code == 200, r.text
    after = r.json()
    assert {c["name"]: c["status"] for c in after["checks"]}["invoice_totals"] == "pass"
    assert next(x for x in after["fields"] if x["name"] == "total_ttc")["source"] == "corrected"
    bad = client.patch(f"/api/v1/documents/{body['id']}/fields/{f['id']}", json={"value": "abc"}, headers=h)
    assert bad.status_code == 422


def test_rejects_unsafe_or_unsupported_files(client, tenants):
    org = tenants.org()
    h = login(client, tenants.user(org, "credit_analyst"))
    assert _upload(client, h, b"hello world, not a document", "x.txt", "text/plain").json()["error"]["code"] == "unsupported_type"
    js_pdf = _plain_pdf("Facture").replace(b"/Type /Catalog", b"/Type /Catalog /OpenAction << /S /JavaScript /JS (app.alert(1)) >>")
    r = _upload(client, h, js_pdf, "evil.pdf")
    assert r.status_code == 422 and r.json()["error"]["code"] in ("active_content", "corrupted")
    assert _upload(client, h, b"%PDF-1.4 broken", "broken.pdf").status_code == 422
    exe_named = _upload(client, h, synth.generate("payslip", 0, seed=3).pdf, "payslip.exe")
    assert exe_named.status_code == 422 and exe_named.json()["error"]["code"] == "extension_mismatch"


def test_four_eyes_review_and_permissions(client, tenants):
    org = tenants.org()
    uploader = tenants.user(org, "credit_analyst")
    reviewer = tenants.user(org, "credit_analyst")
    fraud = tenants.user(org, "fraud_analyst")
    h = login(client, uploader)
    body = client.post("/api/v1/documents/specimens", json={"doc_type": "company_extract"}, headers=h).json()
    doc_id = body["id"]
    assert body["is_demo"] is True and body["filename"].startswith("DEMO_")
    if body["doc_type"] is None:
        client.post(f"/api/v1/documents/{doc_id}/reprocess", json={"doc_type": "company_extract"}, headers=h)
    r = client.post(f"/api/v1/documents/{doc_id}/review", json={"decision": "validated", "comment": "ok pour moi"}, headers=h)
    assert r.status_code == 403 and r.json()["error"]["code"] == "four_eyes"
    client.cookies.clear()
    h2 = login(client, reviewer)
    r = client.post(f"/api/v1/documents/{doc_id}/review", json={"decision": "validated", "comment": "contrôlé, conforme"}, headers=h2)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "validated" and r.json()["validated_by"] == reviewer["user_id"]
    again = client.post(f"/api/v1/documents/{doc_id}/review", json={"decision": "rejected", "comment": "second avis"}, headers=h2)
    assert again.status_code == 409
    client.cookies.clear()
    h3 = login(client, fraud)
    assert _upload(client, h3, synth.generate("payslip", 0, seed=4).pdf, "p.pdf").status_code == 403
    assert client.get(f"/api/v1/documents/{doc_id}").status_code == 200  # fraud analysts may read documents


def test_documents_are_isolated_between_tenants(client, tenants):
    a, b = tenants.org(), tenants.org()
    ha = login(client, tenants.user(a, "credit_analyst"))
    doc = client.post("/api/v1/documents/specimens", json={"doc_type": "utility_bill"}, headers=ha).json()
    client.cookies.clear()
    hb = login(client, tenants.user(b, "credit_analyst"))
    assert client.get(f"/api/v1/documents/{doc['id']}").status_code == 404
    assert client.get(f"/api/v1/documents/{doc['id']}/pages/1/image").status_code == 404
    assert client.get(f"/api/v1/documents/{doc['id']}/file").status_code == 404
    assert client.post(f"/api/v1/documents/{doc['id']}/review", json={"decision": "rejected", "comment": "intrusion"},
                       headers=hb).status_code == 404
    assert all(i["id"] != doc["id"] for i in client.get("/api/v1/documents").json()["items"])
    # the same file can exist in two tenants (deduplication is per organisation)
    data = synth.generate("invoice", 2, seed=99).pdf
    client.cookies.clear()
    ha = login(client, tenants.user(a, "credit_analyst"))
    assert _upload(client, ha, data, "inv.pdf").status_code == 201
    client.cookies.clear()
    hb = login(client, tenants.user(b, "credit_analyst"))
    assert _upload(client, hb, data, "inv.pdf").status_code == 201


def test_demo_org_refuses_real_documents_and_llm_is_explicitly_unavailable(client, tenants):
    org = tenants.org("demo", kind="demo")
    h = login(client, tenants.user(org, "credit_analyst"))
    r = _upload(client, h, _plain_pdf("Avis d'imposition 2026 - Jean Martin - revenu fiscal de reference 41 250"), "avis.pdf")
    assert r.status_code == 422 and r.json()["error"]["code"] == "demo_real_document_refused"
    assert client.get("/api/v1/documents").json()["total"] == 0
    ok = client.post("/api/v1/documents/specimens", json={"doc_type": "bank_statement", "with_anomaly": True}, headers=h)
    assert ok.status_code == 201
    assert ok.json()["specimen"]["injected_anomalies"]
    an = client.post(f"/api/v1/documents/{ok.json()['id']}/analyze", headers=h)
    assert an.status_code == 503 and an.json()["error"]["code"] == "llm_unavailable"
