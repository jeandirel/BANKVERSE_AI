"""Synthetic DEMO specimen documents (PDF via ReportLab; 'scanned' PNG variants via PyMuPDF + OpenCV).

Every document is watermarked « SPECIMEN — DEMO » and describes fictitious people and companies.
No identity document (ID card, passport) is ever generated. Several layout templates per type allow
an honest template-level evaluation of the classifier (train on some layouts, test on an unseen one).
Some documents deliberately contain inconsistencies (wrong totals, invalid IBAN) to exercise checks.
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import cv2
import numpy as np
import pymupdf as fitz
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from bankverse.demo.generator import CITIES, FIRST, LAST
from bankverse.demo.ibans import format_iban, make_fr_iban

DOC_TYPES = ("bank_statement", "invoice", "payslip", "company_extract", "utility_bill")
TEMPLATES = (0, 1, 2)
DOC_LABELS = {"bank_statement": "Relevé de compte", "invoice": "Facture", "payslip": "Bulletin de paie",
              "company_extract": "Extrait d'immatriculation", "utility_bill": "Facture d'énergie (justificatif de domicile)"}


REFERENCE_DATE = date(2026, 10, 15)  # fixed 'today' for the synthetic corpus (reproducible evaluation)


@dataclass
class SynthDoc:
    doc_type: str
    template: int
    pdf: bytes
    truth: dict[str, Any] = field(default_factory=dict)
    defects: list[str] = field(default_factory=list)


def _siren(rng: random.Random) -> str:
    base = [rng.randint(0, 9) for _ in range(8)]
    for check in range(10):
        digits = [*base, check]
        total = 0
        for i, d in enumerate(reversed(digits)):
            v = d * 2 if i % 2 == 1 else d
            total += v - 9 if v > 9 else v
        if total % 10 == 0:
            return "".join(map(str, digits))
    return "".join(map(str, base)) + "0"


def _eur(v: float) -> str:
    s = f"{v:,.2f}".replace(",", " ").replace(".", ",")
    return f"{s} €"


def _watermark(c: canvas.Canvas) -> None:
    c.saveState()
    c.setFillColor(colors.Color(0.75, 0.2, 0.15, alpha=0.18))
    c.setFont("Helvetica-Bold", 46)
    c.translate(300, 420)
    c.rotate(35)
    c.drawCentredString(0, 0, "SPECIMEN — DEMO")
    c.restoreState()
    c.setFont("Helvetica", 7)
    c.setFillColor(colors.grey)
    c.drawString(15 * mm, 8 * mm, "Document fictif généré par BANKVERSE AI à des fins de démonstration. Sans valeur légale.")


def _person(rng: random.Random) -> tuple[str, str]:
    city, pc, _, _ = rng.choice(CITIES)
    return f"{rng.choice(FIRST)} {rng.choice(LAST)}", f"{rng.randint(1, 140)} rue {rng.choice(['de la République', 'Victor Hugo', 'des Lilas', 'Pasteur', 'Jean Jaurès'])}, {pc} {city}"


def _company(rng: random.Random) -> tuple[str, str]:
    city, pc, _, _ = rng.choice(CITIES)
    name = f"{rng.choice(LAST)} {rng.choice(['Conseil', 'Logistique', 'Bâtiment', 'Numérique', 'Distribution'])} SAS"
    return name, f"{rng.randint(1, 90)} avenue {rng.choice(['du Général Leclerc', 'de la Gare', 'Foch', 'des Champs'])}, {pc} {city}"


def bank_statement(rng: random.Random, tpl: int, defect: bool) -> SynthDoc:
    holder, addr = _person(rng)
    iban = make_fr_iban(rng)
    if defect:
        iban = iban[:-1] + str((int(iban[-1]) + 1) % 10)
    start = date(2026, rng.randint(1, 8), 1)
    end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    opening = round(rng.uniform(200, 6000), 2)
    ops = []
    for _ in range(rng.randint(6, 12)):
        amt = round(rng.uniform(5, 900), 2)
        credit = rng.random() < 0.25
        ops.append((start + timedelta(days=rng.randint(0, (end - start).days)), rng.choice(
            ["CB SUPERMARCHE", "PRLV ENERGIE", "VIR SALAIRE", "CB RESTAURANT", "RETRAIT DAB", "VIR LOYER", "CB PHARMACIE"]), amt, credit))
    ops.sort()
    debits = round(sum(a for _, _, a, cr in ops if not cr), 2)
    credits = round(sum(a for _, _, a, cr in ops if cr), 2)
    closing = round(opening + credits - debits, 2)
    shown_closing = closing + (round(rng.uniform(10, 300), 2) if defect and rng.random() < 0.5 else 0)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    title = ["RELEVÉ DE COMPTE", "Relevé de compte courant", "EXTRAIT DE COMPTE"][tpl]
    x0 = [20, 25, 18][tpl] * mm
    c.setFont("Helvetica-Bold", [16, 14, 15][tpl])
    c.drawString(x0, 275 * mm, "Banque Démo BANKVERSE" if tpl != 1 else "BANKVERSE — Banque de démonstration")
    c.setFont("Helvetica-Bold", 12)
    c.drawString(x0, 262 * mm, title)
    c.setFont("Helvetica", 10)
    y = 250 * mm
    for line in ([f"Titulaire : {holder}", addr, f"IBAN : {format_iban(iban)}", "BIC : DEMOFRPPXXX",
                  f"Période du {start:%d/%m/%Y} au {end:%d/%m/%Y}"] if tpl != 2 else
                 [f"Période : {start:%d/%m/%Y} - {end:%d/%m/%Y}", f"Compte de {holder}", addr, f"IBAN {format_iban(iban)}"]):
        c.drawString(x0, y, line)
        y -= 6 * mm
    y -= 4 * mm
    c.drawString(x0, y, f"Solde au {start:%d/%m/%Y} : {_eur(opening)}" if tpl != 1 else f"Ancien solde : {_eur(opening)}")
    y -= 10 * mm
    c.setFont("Helvetica-Bold", 9)
    for i, h in enumerate(["Date", "Libellé", "Débit", "Crédit"]):
        c.drawString(x0 + [0, 28, 110, 145][i] * mm, y, h)
    c.setFont("Helvetica", 9)
    for d, lab, amt, cr in ops:
        y -= 6 * mm
        c.drawString(x0, y, d.strftime("%d/%m/%Y"))
        c.drawString(x0 + 28 * mm, y, lab)
        c.drawString(x0 + (145 if cr else 110) * mm, y, _eur(amt))
    y -= 10 * mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x0, y, f"Total des débits : {_eur(debits)}")
    c.drawString(x0 + 90 * mm, y, f"Total des crédits : {_eur(credits)}")
    y -= 7 * mm
    c.drawString(x0, y, f"Solde au {end:%d/%m/%Y} : {_eur(shown_closing)}" if tpl != 1 else f"Nouveau solde : {_eur(shown_closing)}")
    _watermark(c)
    c.save()
    return SynthDoc("bank_statement", tpl, buf.getvalue(),
                    {"holder": holder, "iban": iban, "period_start": start.isoformat(), "period_end": end.isoformat(), "opening_balance": opening,
                     "closing_balance": shown_closing, "total_debits": debits, "total_credits": credits},
                    ["invalid_iban" if defect else ""] + (["balance_mismatch"] if shown_closing != closing else []))


def invoice(rng: random.Random, tpl: int, defect: bool) -> SynthDoc:
    seller, s_addr = _company(rng)
    buyer, b_addr = _person(rng)
    siren = _siren(rng)
    num = f"F-2026-{rng.randint(1000, 9999)}"
    d = date(2026, rng.randint(1, 9), rng.randint(1, 28))
    lines = [(rng.choice(["Prestation de conseil", "Licence logicielle", "Maintenance", "Formation", "Matériel informatique"]),
              rng.randint(1, 5), round(rng.uniform(80, 1500), 2)) for _ in range(rng.randint(1, 5))]
    ht = round(sum(q * p for _, q, p in lines), 2)
    rate = rng.choice([0.2, 0.2, 0.1, 0.055])
    tva = round(ht * rate, 2)
    ttc = round(ht + tva + (round(rng.uniform(5, 80), 2) if defect else 0), 2)
    iban = make_fr_iban(rng, bank="99996")
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    x0 = [20, 22, 25][tpl] * mm
    c.setFont("Helvetica-Bold", 15)
    c.drawString(x0, 275 * mm, seller)
    c.setFont("Helvetica", 9)
    c.drawString(x0, 269 * mm, s_addr)
    c.drawString(x0, 264 * mm, f"SIREN {siren}" if tpl != 1 else f"SIRET {siren}{rng.randint(10000, 99999)}")
    c.setFont("Helvetica-Bold", 13)
    c.drawString([130, 20, 130][tpl] * mm, [275, 250, 255][tpl] * mm, ["FACTURE", "Facture", "FACTURE N°"][tpl] + ("" if tpl != 2 else f" {num}"))
    c.setFont("Helvetica", 10)
    y = 240 * mm
    meta = [f"Facture n° {num}", f"Date : {d:%d/%m/%Y}", f"Client : {buyer}", b_addr] if tpl != 2 else [f"Émise le {d:%d/%m/%Y}", f"Destinataire : {buyer}", b_addr]
    for line in meta:
        c.drawString(x0, y, line)
        y -= 6 * mm
    y -= 6 * mm
    c.setFont("Helvetica-Bold", 9)
    for i, h in enumerate(["Désignation", "Qté", "Prix unitaire HT", "Montant HT"]):
        c.drawString(x0 + [0, 85, 105, 145][i] * mm, y, h)
    c.setFont("Helvetica", 9)
    for lab, q, p in lines:
        y -= 6 * mm
        c.drawString(x0, y, lab)
        c.drawString(x0 + 85 * mm, y, str(q))
        c.drawString(x0 + 105 * mm, y, _eur(p))
        c.drawString(x0 + 145 * mm, y, _eur(q * p))
    y -= 12 * mm
    c.setFont("Helvetica-Bold", 10)
    for lab, v in ((["Total HT", "Montant HT", "Total hors taxes"][tpl], ht), (f"TVA {rate * 100:g} %".replace(".", ","), tva), (["Total TTC", "Net à payer TTC", "Total TTC"][tpl], ttc)):
        c.drawString(x0 + 105 * mm, y, f"{lab} : {_eur(v)}")
        y -= 7 * mm
    c.setFont("Helvetica", 9)
    c.drawString(x0, y - 6 * mm, f"Règlement par virement — IBAN {format_iban(iban)}")
    _watermark(c)
    c.save()
    return SynthDoc("invoice", tpl, buf.getvalue(), {"invoice_number": num, "invoice_date": d.isoformat(), "seller": seller, "siren": siren,
                                                     "total_ht": ht, "vat": tva, "total_ttc": ttc, "iban": iban},
                    ["totals_mismatch"] if defect else [])


def payslip(rng: random.Random, tpl: int, defect: bool) -> SynthDoc:
    employer, e_addr = _company(rng)
    emp, addr = _person(rng)
    month = date(2026, rng.randint(1, 9), 1)
    gross = round(rng.uniform(1900, 6500), 2)
    contrib = round(gross * rng.uniform(0.21, 0.24), 2)
    net = round(gross - contrib, 2)
    tax = round(net * rng.uniform(0.0, 0.11), 2)
    payable = round(net - tax, 2)
    if defect:
        net = round(gross * 1.05, 2)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    x0 = [20, 18, 24][tpl] * mm
    c.setFont("Helvetica-Bold", 14)
    c.drawString(x0, 275 * mm, ["BULLETIN DE PAIE", "Bulletin de salaire", "FICHE DE PAIE"][tpl])
    c.setFont("Helvetica", 10)
    y = 262 * mm
    for line in [f"Employeur : {employer}", e_addr, f"SIREN {_siren(rng)}", f"Salarié : {emp}", addr,
                 f"Période : {['', 'mois de '][tpl % 2]}{month:%m/%Y}", f"Emploi : {rng.choice(['Comptable', 'Technicien', 'Chargé de clientèle', 'Ingénieur'])}"]:
        c.drawString(x0, y, line)
        y -= 6 * mm
    y -= 8 * mm
    c.setFont("Helvetica", 10)
    for lab, v in (("Salaire de base brut" if tpl != 1 else "Salaire brut", gross), ("Total cotisations salariales", contrib),
                   ("Net à payer avant impôt sur le revenu" if tpl != 2 else "Net avant impôt", net), ("Impôt sur le revenu prélevé à la source", tax),
                   ("Net payé" if tpl != 0 else "NET À PAYER", payable)):
        c.drawString(x0, y, lab)
        c.drawRightString(x0 + 165 * mm, y, _eur(v))
        y -= 7 * mm
    _watermark(c)
    c.save()
    return SynthDoc("payslip", tpl, buf.getvalue(), {"employer": employer, "employee": emp, "period": month.strftime("%Y-%m"),
                                                     "gross_salary": gross, "net_salary": net, "net_paid": payable},
                    ["net_above_gross"] if defect else [])


def company_extract(rng: random.Random, tpl: int, defect: bool) -> SynthDoc:
    name, addr = _company(rng)
    siren = _siren(rng)
    if defect:
        siren = siren[:-1] + str((int(siren[-1]) + 1) % 10)
    reg = date(rng.randint(1995, 2024), rng.randint(1, 12), rng.randint(1, 28))
    capital = rng.choice([1000, 5000, 10000, 50000, 150000])
    manager, _ = _person(rng)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    x0 = [20, 25, 20][tpl] * mm
    c.setFont("Helvetica-Bold", 13)
    c.drawString(x0, 275 * mm, ["EXTRAIT D'IMMATRICULATION — SPECIMEN", "Extrait du registre (spécimen)", "FICHE D'IDENTIFICATION ENTREPRISE"][tpl])
    c.setFont("Helvetica", 10)
    y = 258 * mm
    for lab, v in (("Dénomination", name), ("Forme juridique", "Société par actions simplifiée"), ("Capital social", _eur(capital)),
                   ("Adresse du siège", addr), ("SIREN" if tpl != 2 else "Numéro SIREN", siren), ("Date d'immatriculation", reg.strftime("%d/%m/%Y")),
                   ("Président" if tpl != 1 else "Dirigeant", manager), ("Activité", rng.choice(["Conseil en gestion", "Transport de marchandises", "Édition de logiciels"]))):
        c.drawString(x0, y, f"{lab} : {v}")
        y -= 8 * mm
    _watermark(c)
    c.save()
    return SynthDoc("company_extract", tpl, buf.getvalue(), {"company_name": name, "siren": siren, "registration_date": reg.isoformat(),
                                                             "capital": capital}, ["invalid_siren"] if defect else [])


def utility_bill(rng: random.Random, tpl: int, defect: bool) -> SynthDoc:
    customer, addr = _person(rng)
    # Issue dates are relative to REFERENCE_DATE so the freshness control is reproducible.
    d = REFERENCE_DATE - timedelta(days=rng.randint(10, 80)) if not defect else REFERENCE_DATE - timedelta(days=rng.randint(200, 400))
    amount = round(rng.uniform(35, 260), 2)
    provider = ["Énergie Démo SA", "Électricité DEMO", "Gaz & Lumière DEMO"][tpl]
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    x0 = [20, 22, 20][tpl] * mm
    c.setFont("Helvetica-Bold", 14)
    c.drawString(x0, 275 * mm, provider)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(x0, 265 * mm, ["Facture d'électricité", "Votre facture d'énergie", "FACTURE GAZ ET ÉLECTRICITÉ"][tpl])
    c.setFont("Helvetica", 10)
    y = 250 * mm
    for line in [f"Titulaire du contrat : {customer}", f"Adresse de consommation : {addr}", f"Date de facture : {d:%d/%m/%Y}",
                 f"Référence client : {rng.randint(10**9, 10**10 - 1)}", f"Consommation : {rng.randint(120, 900)} kWh",
                 f"Montant TTC à payer : {_eur(amount)}"]:
        c.drawString(x0, y, line)
        y -= 7 * mm
    _watermark(c)
    c.save()
    return SynthDoc("utility_bill", tpl, buf.getvalue(), {"customer": customer, "address": addr, "bill_date": d.isoformat(), "amount": amount},
                    ["stale_document"] if defect else [])


GENERATORS = {"bank_statement": bank_statement, "invoice": invoice, "payslip": payslip, "company_extract": company_extract,
              "utility_bill": utility_bill}


def generate(doc_type: str, tpl: int, seed: int, defect_rate: float = 0.25) -> SynthDoc:
    rng = random.Random(seed)
    doc = GENERATORS[doc_type](rng, tpl, rng.random() < defect_rate)
    doc.defects = [d for d in doc.defects if d]
    return doc


def to_scan(pdf: bytes, seed: int, dpi: int = 150) -> bytes:
    """Rasterises page 1 and degrades it like a phone scan (rotation, blur, noise, contrast) → PNG."""
    rng = np.random.default_rng(seed)
    with fitz.open(stream=pdf, filetype="pdf") as d:
        pix = d[0].get_pixmap(dpi=dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[..., :3].copy()
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    angle = float(rng.uniform(-2.5, 2.5))
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    gray = cv2.warpAffine(gray, M, (w, h), borderValue=255)
    gray = cv2.GaussianBlur(gray, (3, 3), float(rng.uniform(0.2, 0.9)))
    noise = rng.normal(0, rng.uniform(4, 10), gray.shape)
    gray = np.clip(gray.astype(float) * rng.uniform(0.85, 1.0) + noise + rng.uniform(0, 20), 0, 255).astype(np.uint8)
    ok, png = cv2.imencode(".png", gray)
    assert ok
    return png.tobytes()
