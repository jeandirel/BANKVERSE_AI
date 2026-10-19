"""Document AI engine on generated DEMO specimens: extraction, positioned fields and consistency controls."""

from __future__ import annotations

from datetime import date

import pytest

from bankverse.core.errors import ValidationFailed
from bankverse.modules.documents import process, synth


@pytest.mark.parametrize("doc_type", synth.DOC_TYPES)
@pytest.mark.parametrize("tpl", [0, 1, 2])
def test_native_pdf_extraction_matches_ground_truth(doc_type: str, tpl: int) -> None:
    d = synth.generate(doc_type, tpl, seed=11 + tpl, defect_rate=0.0)
    pages = process.extract(d.pdf, process.validate_upload(d.pdf, "x.pdf", 10_000_000))
    fields = {f["name"]: f for f in process.extract_fields(doc_type, pages)}
    for name, truth in d.truth.items():
        if name not in fields:
            continue
        got = fields[name]["normalized"]
        if isinstance(truth, (int, float)):
            assert got is not None and abs(float(got) - truth) <= 0.011, (name, got, truth)
        elif name == "iban":
            assert got == truth
    checks = {c["check_name"]: c["status"] for c in process.run_checks(doc_type, list(fields.values()), today=synth.REFERENCE_DATE)}
    assert "fail" not in checks.values(), checks
    assert any(f["bbox"] for f in fields.values())


@pytest.mark.parametrize(("doc_type", "check"), [("bank_statement", "iban_checksum"), ("invoice", "invoice_totals"),
                                                 ("payslip", "net_not_above_gross"), ("company_extract", "siren_luhn"),
                                                 ("utility_bill", "document_freshness")])
def test_injected_anomalies_are_detected(doc_type: str, check: str) -> None:
    d = synth.generate(doc_type, 0, seed=5, defect_rate=1.0)
    pages = process.extract(d.pdf, "application/pdf")
    fields = process.extract_fields(doc_type, pages)
    statuses = {c["check_name"]: c["status"] for c in process.run_checks(doc_type, fields, today=synth.REFERENCE_DATE)}
    assert statuses.get(check) == "fail", (d.defects, statuses)


def test_siren_luhn_and_amount_parsing() -> None:
    assert process.siren_valid("732829320") and not process.siren_valid("732829321")
    assert process.parse_amount("1 234,56 €") == 1234.56 and process.parse_amount("1234.56") == 1234.56
    assert process.parse_date("31/12/2026") == date(2026, 12, 31)


def test_upload_validation_rejects_unsafe_files() -> None:
    with pytest.raises(ValidationFailed):
        process.validate_upload(b"", "a.pdf", 1000)
    with pytest.raises(ValidationFailed):
        process.validate_upload(b"MZ\x90\x00" + b"\x00" * 100, "a.exe", 10_000)
    with pytest.raises(ValidationFailed):
        process.validate_upload(b"%PDF-1.4\n%%EOF", "a.pdf", 10_000)
    big = synth.generate("invoice", 0, seed=1).pdf
    with pytest.raises(ValidationFailed):
        process.validate_upload(big, "a.pdf", 100)
