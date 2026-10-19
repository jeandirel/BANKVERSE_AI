"""Document AI processing: secure validation → text/OCR with word boxes → classification → field
extraction (with bounding boxes) → consistency checks. Human validation is always required: the
platform never certifies a document (this is NOT a regulatory identity verification)."""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import cv2
import filetype
import pymupdf as fitz
import numpy as np
import pytesseract

from bankverse.core.errors import ValidationFailed
from bankverse.demo.ibans import is_valid_iban

ALLOWED = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg", "image/tiff": "tiff"}
MAX_PAGES = 20
OCR_DPI = 200


@dataclass
class Word:
    text: str
    bbox: tuple[float, float, float, float]  # page points (PDF user space, origin top-left)
    conf: float = 100.0


@dataclass
class Page:
    no: int
    width: float
    height: float
    words: list[Word]
    text: str
    ocr: bool
    ocr_conf: float | None = None
    tables: list[dict[str, Any]] = field(default_factory=list)


def validate_upload(data: bytes, filename: str, max_bytes: int) -> str:
    if len(data) == 0:
        raise ValidationFailed("Empty file")
    if len(data) > max_bytes:
        raise ValidationFailed(f"File too large (max {max_bytes // (1024 * 1024)} MB)")
    kind = filetype.guess(data)
    if kind is None or kind.mime not in ALLOWED:
        raise ValidationFailed("Unsupported file type (PDF, PNG, JPEG or TIFF only)", code="unsupported_type")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext and ext not in {"pdf", "png", "jpg", "jpeg", "tif", "tiff"}:
        raise ValidationFailed("File extension does not match an allowed type", code="extension_mismatch")
    if kind.mime == "application/pdf":
        try:
            with fitz.open(stream=data, filetype="pdf") as d:
                if d.needs_pass:
                    raise ValidationFailed("Encrypted PDFs are not accepted", code="encrypted_pdf")
                if d.page_count == 0 or d.page_count > MAX_PAGES:
                    raise ValidationFailed(f"PDF must have between 1 and {MAX_PAGES} pages")
                for xref in range(1, d.xref_length()):
                    obj = d.xref_object(xref, compressed=False) or ""
                    if "/JavaScript" in obj or "/JS " in obj or "/Launch" in obj or "/EmbeddedFile" in obj:
                        raise ValidationFailed("Active content (JavaScript, launch actions or embedded files) is not allowed",
                                               code="active_content")
        except ValidationFailed:
            raise
        except Exception as exc:
            raise ValidationFailed("Corrupted PDF", code="corrupted") from exc
    else:
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise ValidationFailed("Corrupted image", code="corrupted")
        if max(img.shape) > 12000:
            raise ValidationFailed("Image dimensions too large")
    return kind.mime


def _skew_angle(gray: np.ndarray) -> float:
    """Projection-profile deskew: the rotation that makes text rows sharpest (max row-sum gradient energy)."""
    scale = 800.0 / max(gray.shape)
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray
    _, inv = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    h, w = inv.shape
    best, best_score = 0.0, -1.0
    for a in np.arange(-5.0, 5.01, 0.25):
        M = cv2.getRotationMatrix2D((w / 2, h / 2), float(a), 1.0)
        rows = cv2.warpAffine(inv, M, (w, h), flags=cv2.INTER_NEAREST, borderValue=0).sum(axis=1, dtype=np.float64)
        score = float(np.sum(np.diff(rows) ** 2))
        if score > best_score:
            best, best_score = float(a), score
    return best


def _preprocess(gray: np.ndarray) -> tuple[np.ndarray, float]:
    """Deskew (projection profile) + denoise + adaptive threshold."""
    angle = _skew_angle(gray)
    if abs(angle) > 0.1:
        h, w = gray.shape
        M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        gray = cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC, borderValue=255)
    gray = cv2.fastNlMeansDenoising(gray, None, h=12)
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11)
    return bw, angle


def _ocr_image(gray: np.ndarray, scale: float) -> tuple[list[Word], float, float]:
    """OCR an image; returns words with boxes converted to page points (scale = points per pixel)."""
    bw, angle = _preprocess(gray)
    data = pytesseract.image_to_data(bw, lang="fra+eng", config="--psm 6", output_type=pytesseract.Output.DICT)
    words, confs = [], []
    for i, txt in enumerate(data["text"]):
        t = (txt or "").strip()
        conf = float(data["conf"][i])
        if not t or conf < 0:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        words.append(Word(t, (x * scale, y * scale, (x + w) * scale, (y + h) * scale), conf))
        confs.append(conf)
    return words, float(np.mean(confs)) if confs else 0.0, angle


def extract(data: bytes, mime: str) -> list[Page]:
    pages: list[Page] = []
    if mime == "application/pdf":
        with fitz.open(stream=data, filetype="pdf") as d:
            for i, pg in enumerate(d):
                raw = pg.get_text("words")
                words = [Word(w[4], (w[0], w[1], w[2], w[3])) for w in raw]
                text = pg.get_text("text")
                if len(text.strip()) < 20:  # no text layer → OCR
                    pix = pg.get_pixmap(dpi=OCR_DPI)
                    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                    gray = cv2.cvtColor(img[..., :3], cv2.COLOR_RGB2GRAY)
                    ow, conf, _ = _ocr_image(gray, 72 / OCR_DPI)
                    pages.append(Page(i + 1, pg.rect.width, pg.rect.height, ow, _join(ow), True, conf))
                    continue
                tables = []
                try:
                    for t in pg.find_tables().tables[:5]:
                        rows = t.extract()
                        if rows and len(rows) > 1:
                            tables.append({"bbox": list(t.bbox), "rows": rows[:60]})
                except Exception:
                    tables = []
                pages.append(Page(i + 1, pg.rect.width, pg.rect.height, words, text, False, None, tables))
    else:
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
        h, w = img.shape
        scale = 595.0 / w  # express boxes in an A4-width coordinate system
        ow, conf, _ = _ocr_image(img, scale)
        pages.append(Page(1, 595.0, h * scale, ow, _join(ow), True, conf))
    return pages


def _join(words: list[Word]) -> str:
    """Rebuild reading-order text lines from word boxes (group by vertical centre)."""
    lines: list[list[Word]] = []
    for w in sorted(words, key=lambda w: ((w.bbox[1] + w.bbox[3]) / 2, w.bbox[0])):
        cy = (w.bbox[1] + w.bbox[3]) / 2
        if lines and abs(((lines[-1][0].bbox[1] + lines[-1][0].bbox[3]) / 2) - cy) < 5:
            lines[-1].append(w)
        else:
            lines.append([w])
    return "\n".join(" ".join(x.text for x in sorted(ln, key=lambda w: w.bbox[0])) for ln in lines)


# ------------------------------------------------------------------------------------------------
# Field extraction
# ------------------------------------------------------------------------------------------------
AMOUNT = r"(-?\d{1,3}(?:[   .]\d{3})*(?:,\d{2})|-?\d+(?:[.,]\d{2}))\s*(?:€|EUR)?"
DATE = r"(\d{2}/\d{2}/\d{4})"

FIELD_RULES: dict[str, list[tuple[str, str, bool]]] = {
    # name, regex (group 1 = value), required
    "bank_statement": [
        ("holder", r"(?:Titulaire\s*:|Compte de)\s*([A-ZÀ-Ü][\wÀ-ÿ'\- ]+?)\s*$", True),
        ("iban", r"IBAN\s*:?\s*([A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){4,7}(?:\s?[A-Z0-9]{1,3})?)", True),
        ("period_start", r"(?:du|Période\s*:)\s*" + DATE, True),
        ("period_end", r"(?:au|-)\s*" + DATE + r"\s*$", True),
        ("opening_balance", r"(?:Ancien solde|Solde au \d{2}/\d{2}/\d{4})\s*:\s*" + AMOUNT, True),
        ("closing_balance", r"(?:Nouveau solde|Solde au \d{2}/\d{2}/\d{4})\s*:\s*" + AMOUNT + r"\s*$", True),
        ("total_debits", r"Total des d[ée]bits\s*:\s*" + AMOUNT, True),
        ("total_credits", r"Total des cr[ée]dits\s*:\s*" + AMOUNT, True),
    ],
    "invoice": [
        ("invoice_number", r"(?:Facture n°|FACTURE N°)\s*([A-Z0-9\-]+)", False),
        ("invoice_date", r"(?:Date\s*:|Émise le)\s*" + DATE, True),
        ("siren", r"SIRE[NT]\s*(\d{9})", True),
        ("total_ht", r"(?:Total HT|Montant HT|Total hors taxes)\s*:\s*" + AMOUNT, True),
        ("vat", r"TVA\s*[\d,]+\s*%\s*:\s*" + AMOUNT, True),
        ("vat_rate", r"TVA\s*([\d,]+)\s*%", False),
        ("total_ttc", r"(?:Total TTC|Net à payer TTC)\s*:\s*" + AMOUNT, True),
        ("iban", r"IBAN\s*([A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){4,7}(?:\s?[A-Z0-9]{1,3})?)", False),
    ],
    "payslip": [
        ("employer", r"Employeur\s*:\s*(.+)$", True),
        ("employee", r"Salarié\s*:\s*(.+)$", True),
        ("period", r"Période\s*:\s*(?:mois de\s*)?(\d{2}/\d{4})", True),
        ("gross_salary", r"Salaire (?:de base )?brut\s*" + AMOUNT, True),
        ("net_salary", r"Net (?:à payer )?avant imp[ôo]t(?: sur le revenu)?\s*" + AMOUNT, True),
        ("net_paid", r"(?:Net payé|NET À PAYER)\s*" + AMOUNT, True),
    ],
    "company_extract": [
        ("company_name", r"Dénomination\s*:\s*(.+)$", True),
        ("siren", r"(?:SIREN|Numéro SIREN)\s*:\s*(\d{9})", True),
        ("legal_form", r"Forme juridique\s*:\s*(.+)$", True),
        ("registration_date", r"Date d'immatriculation\s*:\s*" + DATE, True),
        ("capital", r"Capital social\s*:\s*" + AMOUNT, False),
        ("address", r"Adresse du siège\s*:\s*(.+)$", True),
    ],
    "utility_bill": [
        ("customer", r"Titulaire du contrat\s*:\s*(.+)$", True),
        ("address", r"Adresse de consommation\s*:\s*(.+)$", True),
        ("bill_date", r"Date de facture\s*:\s*" + DATE, True),
        ("amount", r"Montant TTC à payer\s*:\s*" + AMOUNT, True),
    ],
}


LAST_MATCH = {"closing_balance"}  # "Solde au <date>" appears for both balances: the closing one is the last


def parse_amount(s: str) -> float | None:
    t = s.replace(" ", "").replace(" ", "").replace("€", "").replace("EUR", "").strip()
    if "," in t:
        t = t.replace(" ", "").replace(".", "").replace(",", ".")
    else:
        t = t.replace(" ", "")
    try:
        return float(t)
    except ValueError:
        return None


def parse_date(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%d/%m/%Y").date()
    except ValueError:
        return None


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s)


def _locate(value: str, pages: list[Page]) -> tuple[int | None, list[float] | None]:
    tokens = [t for t in re.split(r"\s+", value) if len(t) >= 2][:6]
    if not tokens:
        return None, None
    for p in pages:
        hits = [w for w in p.words if any(_norm(w.text).strip(":,") == _norm(t) for t in tokens)]
        if hits:
            x0 = min(w.bbox[0] for w in hits)
            y0 = min(w.bbox[1] for w in hits)
            x1 = max(w.bbox[2] for w in hits)
            y1 = max(w.bbox[3] for w in hits)
            if (y1 - y0) < 60:  # tokens on (about) the same line
                return p.no, [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)]
            first = hits[0]
            return p.no, [round(v, 1) for v in first.bbox]
    return None, None


def extract_fields(doc_type: str, pages: list[Page]) -> list[dict[str, Any]]:
    text = "\n".join(_norm(p.text) for p in pages)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out = []
    ocr = any(p.ocr for p in pages)
    for name, pattern, required in FIELD_RULES.get(doc_type, []):
        rx = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
        val = None
        for ln in (reversed(lines) if name in LAST_MATCH else lines):
            m = rx.search(ln)
            if m:
                val = m.group(1).strip()
                break
        if val is None:
            m = rx.search(text)
            val = m.group(1).strip() if m else None
        normalized: str | None = None
        if val is not None:
            if name in ("iban",):
                normalized = re.sub(r"\s", "", val).upper()
            elif any(k in name for k in ("balance", "total", "vat", "salary", "net_", "amount", "capital")) and name != "vat_rate":
                a = parse_amount(val)
                normalized = None if a is None else f"{a:.2f}"
            elif name == "period":
                mm = re.fullmatch(r"(\d{2})/(\d{4})", val)
                normalized = f"{mm.group(2)}-{mm.group(1)}" if mm else None
            elif "date" in name or name.startswith("period_"):
                d = parse_date(val)
                normalized = d.isoformat() if d else None
            else:
                normalized = val
        page_no, bbox = _locate(val, pages) if val else (None, None)
        conf = 0.0 if val is None else (0.9 if normalized is not None else 0.5) * (0.85 if ocr else 1.0)
        out.append({"name": name, "value": val, "normalized": normalized, "confidence": round(conf, 3), "page_no": page_no,
                    "bbox": bbox, "required": required})
    return out


def _num(fields: dict[str, dict[str, Any]], k: str) -> float | None:
    v = fields.get(k, {}).get("normalized")
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def siren_valid(s: str) -> bool:
    if not re.fullmatch(r"\d{9}", s or ""):
        return False
    total = 0
    for i, ch in enumerate(reversed(s)):
        v = int(ch) * (2 if i % 2 == 1 else 1)
        total += v - 9 if v > 9 else v
    return total % 10 == 0


def run_checks(doc_type: str, fields_list: list[dict[str, Any]], today: date | None = None) -> list[dict[str, str]]:
    today = today or date.today()
    f = {x["name"]: x for x in fields_list}
    checks: list[dict[str, str]] = []
    missing = [x["name"] for x in fields_list if x["required"] and not x["normalized"]]
    checks.append({"check_name": "required_fields", "status": "fail" if missing else "pass",
                   "detail": f"Champs manquants ou illisibles : {', '.join(missing)}" if missing else "Tous les champs obligatoires sont présents."})
    if "iban" in f and f["iban"]["normalized"]:
        ok = is_valid_iban(f["iban"]["normalized"])
        checks.append({"check_name": "iban_checksum", "status": "pass" if ok else "fail",
                       "detail": "Clé IBAN valide (ISO 13616 mod 97)." if ok else "Clé de contrôle IBAN invalide."})
    if "siren" in f and f["siren"]["normalized"]:
        ok = siren_valid(f["siren"]["normalized"])
        checks.append({"check_name": "siren_luhn", "status": "pass" if ok else "fail",
                       "detail": "SIREN valide (algorithme de Luhn)." if ok else "SIREN invalide (Luhn)."})
    if doc_type == "bank_statement":
        o, c, d, cr = (_num(f, k) for k in ("opening_balance", "closing_balance", "total_debits", "total_credits"))
        if None not in (o, c, d, cr):
            diff = round(o + cr - d - c, 2)  # type: ignore[operator]
            checks.append({"check_name": "balance_reconciliation", "status": "pass" if abs(diff) <= 0.01 else "fail",
                           "detail": f"Solde initial + crédits − débits − solde final = {diff:+.2f} €"})
        ps, pe = f.get("period_start", {}).get("normalized"), f.get("period_end", {}).get("normalized")
        if ps and pe:
            checks.append({"check_name": "period_order", "status": "pass" if ps <= pe else "fail", "detail": f"Période {ps} → {pe}"})
    if doc_type == "invoice":
        ht, tva, ttc = (_num(f, k) for k in ("total_ht", "vat", "total_ttc"))
        if None not in (ht, tva, ttc):
            diff = round(ht + tva - ttc, 2)  # type: ignore[operator]
            checks.append({"check_name": "invoice_totals", "status": "pass" if abs(diff) <= 0.02 else "fail",
                           "detail": f"HT + TVA − TTC = {diff:+.2f} €"})
            rate = round(tva / ht, 4) if ht else None  # type: ignore[operator]
            checks.append({"check_name": "vat_rate_plausible", "status": "pass" if rate and any(abs(rate - r) < 0.002 for r in (0.2, 0.1, 0.055, 0.021)) else "warn",
                           "detail": f"Taux de TVA implicite : {rate * 100 if rate else 0:.2f} %"})
    if doc_type == "payslip":
        g, n = _num(f, "gross_salary"), _num(f, "net_salary")
        if g is not None and n is not None:
            checks.append({"check_name": "net_not_above_gross", "status": "pass" if n <= g else "fail", "detail": f"Brut {g:.2f} € / net {n:.2f} €"})
    if doc_type == "utility_bill":
        bd = f.get("bill_date", {}).get("normalized")
        if bd:
            age = (today - date.fromisoformat(bd)).days
            checks.append({"check_name": "document_freshness", "status": "pass" if 0 <= age <= 92 else "fail",
                           "detail": f"Document daté de {age} jours (justificatif de domicile : < 3 mois attendu)."})
    low = [x["name"] for x in fields_list if x["normalized"] and x["confidence"] < 0.6]
    if low:
        checks.append({"check_name": "low_confidence_fields", "status": "warn", "detail": f"À vérifier : {', '.join(low)}"})
    return checks


def process(data: bytes, mime: str, classifier: Any | None) -> dict[str, Any]:
    t0 = time.perf_counter()
    pages = extract(data, mime)
    text = "\n".join(p.text for p in pages)
    doc_type, conf = None, None
    if classifier is not None and text.strip():
        proba = classifier.predict_proba([text])[0]
        j = int(np.argmax(proba))
        doc_type, conf = str(classifier.classes_[j]), float(proba[j])
    flds = extract_fields(doc_type, pages) if doc_type else []
    checks = run_checks(doc_type, flds) if doc_type else [{"check_name": "classification", "status": "fail", "detail": "Type de document non reconnu"}]
    return {"pages": pages, "text": text, "doc_type": doc_type, "confidence": conf, "fields": flds, "checks": checks,
            "ocr": any(p.ocr for p in pages), "processing_ms": int((time.perf_counter() - t0) * 1000)}
