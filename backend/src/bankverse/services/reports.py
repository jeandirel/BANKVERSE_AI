"""PDF report generation (ReportLab) + storage in the tenant's object-store prefix.

Every report carries: organisation, author, generation timestamp, data provenance, a DEMO banner for
the demo tenant and the mandatory disclaimer that AI outputs are decision-support only.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy.orm import Session

from bankverse.core.security import sha256_hex
from bankverse.core.storage import get_store, org_key
from bankverse.db.models.tenant import Report

INK = colors.HexColor("#0B1220")
MUTED = colors.HexColor("#5B6475")
LINE = colors.HexColor("#D9DEE7")
ACCENT = colors.HexColor("#1F4E8C")
WARN = colors.HexColor("#9A3412")

DISCLAIMER_FR = ("Document d'aide à la décision généré par BANKVERSE AI. Les scores et alertes produits par des "
                 "modèles statistiques ne constituent ni une preuve ni une décision : toute conclusion relève de "
                 "l'analyste habilité. Plateforme expérimentale — données publiques et synthétiques.")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontName="Helvetica-Bold", fontSize=17, textColor=INK,
                                alignment=TA_LEFT, spaceAfter=4),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=11.5, textColor=INK,
                             spaceBefore=10, spaceAfter=4),
        "body": ParagraphStyle("b", parent=base["BodyText"], fontName="Helvetica", fontSize=9, leading=12.5,
                               textColor=INK),
        "muted": ParagraphStyle("m", parent=base["BodyText"], fontName="Helvetica", fontSize=8, leading=11,
                                textColor=MUTED),
        "warn": ParagraphStyle("w", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=8.5, textColor=WARN),
    }


def _table(rows: list[list[Any]], widths: list[float] | None = None) -> Table:
    st = _styles()
    data = [[Paragraph(str(c) if c is not None else "—", st["body"]) for c in r] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF1F6")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, INK),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def build_pdf(*, title: str, subtitle: str, org_name: str, author: str, is_demo: bool,
              sections: list[dict[str, Any]]) -> bytes:
    """sections: [{"heading": str, "paragraphs": [str], "table": [[...]], "widths": [...], "page_break": bool}]"""
    st = _styles()
    buf = io.BytesIO()
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    def on_page(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 10 * mm, f"BANKVERSE AI · {org_name} · {now}")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
        if is_demo:
            canvas.setFillColor(WARN)
            canvas.setFont("Helvetica-Bold", 8)
            canvas.drawRightString(A4[0] - 18 * mm, A4[1] - 10 * mm, "DEMO — DONNÉES FICTIVES")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm, title=title, author=f"BANKVERSE AI — {author}")
    story: list[Any] = [Paragraph(title, st["title"]), Paragraph(subtitle, st["muted"]),
                        Paragraph(f"Organisation : {org_name} · Auteur : {author} · Généré le {now}", st["muted"]),
                        Spacer(1, 4)]
    if is_demo:
        story.append(Paragraph("Rapport établi sur la banque de démonstration : toutes les personnes, comptes et "
                               "opérations sont fictifs.", st["warn"]))
    for sec in sections:
        block: list[Any] = []
        if sec.get("page_break"):
            story.append(PageBreak())
        block.append(Paragraph(sec["heading"], st["h2"]))
        for para in sec.get("paragraphs", []):
            block.append(Paragraph(para, st["body"]))
            block.append(Spacer(1, 2))
        if sec.get("table"):
            block.append(_table(sec["table"], sec.get("widths")))
        story.append(KeepTogether(block) if len(block) < 6 else block[0])
        if len(block) >= 6:
            story.extend(block[1:])
    story += [Spacer(1, 10), Paragraph(DISCLAIMER_FR, st["muted"])]
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


def store_report(db: Session, *, org_id: uuid.UUID, kind: str, title: str, pdf: bytes, created_by: uuid.UUID,
                 source_type: str | None, source_id: str | None, meta: dict[str, Any] | None = None) -> Report:
    rid = uuid.uuid4()
    key = org_key(org_id, "reports", f"{rid}.pdf")
    get_store().put(key, pdf, "application/pdf")
    rep = Report(id=rid, org_id=org_id, kind=kind, title=title, fmt="pdf", storage_key=key, sha256=sha256_hex(pdf),
                 size_bytes=len(pdf), created_by=created_by, source_type=source_type, source_id=source_id,
                 meta=meta or {})
    db.add(rep)
    db.flush()
    return rep
