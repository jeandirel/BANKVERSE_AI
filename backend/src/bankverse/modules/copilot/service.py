"""Copilot serving: guardrails → hybrid retrieval (PostgreSQL full-text + pgvector, fused by RRF) → learned
reranking of facts on candidate pages → extractive answer with citations (or grounded LLM synthesis when a
provider is configured) → stored conversation."""

from __future__ import annotations

import io
import re
import time
import uuid
from collections import defaultdict
from typing import Any

import numpy as np
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from bankverse.core.errors import AppError, Unavailable
from bankverse.db.models.platform import PublicChunk, PublicDocument
from bankverse.db.models.tenant import Document, KbChunk
from bankverse.db.session import control_session
from bankverse.ml import registry
from bankverse.modules.copilot import guardrails, llm
from bankverse.modules.copilot.retrieval import Retriever, content_terms

MODEL_NAME = "copilot_retriever"
CANDIDATES = 60
PAGES = 3
EVIDENCE = 5
MIN_LEXICAL_TERMS = 1


def retriever() -> tuple[Retriever, str]:
    try:
        m = registry.load_production(MODEL_NAME)
    except AppError as exc:
        raise Unavailable("The copilot retriever has no approved production version yet", code="model_not_deployed") from exc
    return m.payload, m.tag


def _tsquery(question: str) -> str | None:
    terms = sorted({re.sub(r"[^a-z0-9]", "", t) for t in content_terms(question)} - {""})
    return " | ".join(terms[:30]) if len(terms) >= MIN_LEXICAL_TERMS else None


def _fuse(lex: list[tuple[uuid.UUID, float]], vec: list[tuple[uuid.UUID, float]]) -> dict[uuid.UUID, float]:
    score: dict[uuid.UUID, float] = defaultdict(float)
    for rank, (cid, _) in enumerate(lex):
        score[cid] += 1.0 / (61 + rank)
    for rank, (cid, _) in enumerate(vec):
        score[cid] += 1.0 / (61 + rank)
    return score


def search_public(ret: Retriever, question: str, company: str | None = None, year: int | None = None) -> list[dict[str, Any]]:
    qe = ret.embed([question])[0]
    tsq = _tsquery(question)
    with control_session() as db:
        base = select(PublicChunk.id, PublicChunk.document_id).join(PublicDocument, PublicDocument.id == PublicChunk.document_id)
        if company:
            base = base.where(PublicDocument.company == company.upper())
        if year:
            base = base.where(PublicDocument.fiscal_year == year)
        lex: list[tuple[uuid.UUID, float]] = []
        if tsq:
            q = func.to_tsquery("english", tsq)
            rank = func.ts_rank_cd(PublicChunk.tsv, q)
            lex = [(r[0], float(r[2])) for r in db.execute(base.add_columns(rank).where(PublicChunk.tsv.op("@@")(q))
                                                           .order_by(rank.desc()).limit(CANDIDATES)).all()]
        dist = PublicChunk.embedding.cosine_distance(qe.tolist())
        vec = [(r[0], float(r[2])) for r in db.execute(base.add_columns(dist).where(PublicChunk.embedding.is_not(None))
                                                       .order_by(dist).limit(CANDIDATES)).all()]
        fused = _fuse(lex, vec)
        if not fused:
            return []
        doc_of = dict(db.execute(select(PublicChunk.id, PublicChunk.document_id).where(PublicChunk.id.in_(list(fused)))).all())
        page_score: dict[uuid.UUID, float] = defaultdict(float)
        for cid, s in sorted(fused.items(), key=lambda kv: -kv[1])[:30]:
            page_score[doc_of[cid]] += s
        top_pages = [d for d, _ in sorted(page_score.items(), key=lambda kv: -kv[1])[:PAGES]]
        docs = {d.id: d for d in db.execute(select(PublicDocument).where(PublicDocument.id.in_(top_pages))).scalars()}
        chunks = db.execute(select(PublicChunk).where(PublicChunk.document_id.in_(top_pages)).order_by(PublicChunk.ordinal)).scalars().all()
        facts = []
        per_doc: dict[uuid.UUID, int] = defaultdict(int)
        for c in chunks:
            per_doc[c.document_id] += 1
        seen: dict[uuid.UUID, int] = defaultdict(int)
        for c in chunks:
            d = docs[c.document_id]
            facts.append({"id": str(c.id), "content": c.content, "kind": c.kind, "rel_pos": seen[c.document_id] / max(per_doc[c.document_id] - 1, 1),
                          "embedding": np.asarray(c.embedding, dtype=np.float32) if c.embedding is not None else None,
                          "scope": "public", "document_id": str(d.id), "doc_ref": d.doc_ref, "title": d.title, "company": d.company,
                          "fiscal_year": d.fiscal_year, "page": d.page, "ind": c.meta.get("ind"), "page_score": page_score[d.id]})
            seen[c.document_id] += 1
    return facts


def search_private(db: Session, ret: Retriever, question: str) -> list[dict[str, Any]]:
    """Bank knowledge base (tenant schema, RLS-scoped). French text: lexical 'simple' config + LSA vectors."""
    qe = ret.embed([question])[0]
    terms = sorted({re.sub(r"[^\wà-ÿ]", "", t.lower()) for t in re.findall(r"[\wÀ-ÿ']+", question) if len(t) > 2} - {""})
    lex: list[tuple[uuid.UUID, float]] = []
    if terms:
        q = func.to_tsquery("simple", " | ".join(terms[:30]))
        rank = func.ts_rank_cd(KbChunk.tsv, q)
        lex = [(r[0], float(r[1])) for r in db.execute(select(KbChunk.id, rank).where(KbChunk.tsv.op("@@")(q)).order_by(rank.desc())
                                                       .limit(CANDIDATES)).all()]
    dist = KbChunk.embedding.cosine_distance(qe.tolist())
    vec = [(r[0], float(r[1])) for r in db.execute(select(KbChunk.id, dist).where(KbChunk.embedding.is_not(None)).order_by(dist)
                                                   .limit(CANDIDATES)).all()]
    fused = _fuse(lex, vec)
    if not fused:
        return []
    top = sorted(fused.items(), key=lambda kv: -kv[1])[:20]
    rows = {c.id: (c, d) for c, d in db.execute(select(KbChunk, Document).join(Document, Document.id == KbChunk.document_id)
                                                 .where(KbChunk.id.in_([cid for cid, _ in top]))).all()}
    out = []
    for cid, s in top:
        c, d = rows[cid]
        out.append({"id": str(c.id), "content": c.content, "kind": "text", "rel_pos": 0.0, "embedding": np.asarray(c.embedding, dtype=np.float32)
                    if c.embedding is not None else None, "scope": "private", "document_id": str(d.id), "doc_ref": d.filename,
                    "title": d.filename, "company": None, "fiscal_year": None, "page": c.page_no, "ind": f"chunk_{c.ordinal}",
                    "page_score": s, "lexical": cid in dict(lex)})
    return out


def rank_evidence(ret: Retriever, question: str, facts: list[dict[str, Any]], k: int = EVIDENCE) -> list[dict[str, Any]]:
    if not facts:
        return []
    have = [f["embedding"] is not None for f in facts]
    E = np.vstack([f["embedding"] for f in facts]) if all(have) else None
    scores = ret.rank(question, facts, E)
    order = np.argsort(-scores)[:k]
    out = []
    for i in order:
        f = {kk: v for kk, v in facts[i].items() if kk != "embedding"}
        f["score"] = round(float(scores[i]), 4)
        out.append(f)
    return out


def _citation(f: dict[str, Any], n: int) -> dict[str, Any]:
    return {"n": n, "chunk_id": f["id"], "scope": f["scope"], "document_id": f["document_id"], "doc_ref": f["doc_ref"], "title": f["title"],
            "company": f["company"], "fiscal_year": f["fiscal_year"], "page": f["page"], "kind": f["kind"], "ind": f["ind"],
            "content": f["content"], "score": f["score"]}


def answer(db: Session, question: str, *, scope: str, company: str | None, year: int | None,
           history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    t0 = time.perf_counter()
    verdict = guardrails.check_input(question)
    if verdict.status != "ok":
        return {"status": verdict.status, "mode": "guardrail", "content": verdict.message, "citations": [], "reason": verdict.reason,
                "latency_ms": int((time.perf_counter() - t0) * 1000)}
    ret, tag = retriever()
    facts: list[dict[str, Any]] = []
    if scope in ("public", "all"):
        facts += search_public(ret, question, company, year)
    if scope in ("private", "all"):
        facts += search_private(db, ret, question)
    evidence = rank_evidence(ret, question, facts)
    flagged = [e for e in evidence if guardrails.evidence_is_suspicious(e["content"])]
    evidence = [e for e in evidence if e not in flagged]
    lexical_support = bool(content_terms(question) & set().union(*(content_terms(e["content"]) for e in evidence))) if evidence else False
    citations = [_citation(e, i + 1) for i, e in enumerate(evidence)]
    meta = {"retriever": tag, "scope": scope, "company": company, "year": year, "candidates": len(facts), "dropped_suspicious": len(flagged)}
    if not evidence or not lexical_support:
        return {"status": "insufficient_evidence", "mode": "extractive", "citations": citations[:2], "meta": meta,
                "content": "Je n'ai pas trouvé de passage suffisamment pertinent dans les sources autorisées. Précisez la société, "
                           "l'exercice ou reformulez la question.", "latency_ms": int((time.perf_counter() - t0) * 1000)}
    if llm.available():
        try:
            content = llm.generate(question, [{"source": c["title"], "content": c["content"]} for c in citations], history)
            if re.search(r"\[\d+\]", content):
                meta["llm"] = llm.provider_label()
                return {"status": "answered", "mode": "llm", "content": content, "citations": citations, "meta": meta,
                        "latency_ms": int((time.perf_counter() - t0) * 1000)}
            meta["llm_rejected"] = "answer without citations"
        except Exception as exc:  # provider outage → extractive fallback, explicitly flagged
            meta["llm_error"] = type(exc).__name__
    lines = [f"[{c['n']}] {c['content']}" for c in citations]
    content = ("Passages les plus pertinents trouvés (mode extractif : aucune synthèse générative n'est configurée dans cet "
               "environnement) :\n\n" + "\n".join(lines))
    return {"status": "answered", "mode": "extractive", "content": content, "citations": citations, "meta": meta,
            "latency_ms": int((time.perf_counter() - t0) * 1000)}


# ------------------------------------------------------------------------------------------------
# Indexing
# ------------------------------------------------------------------------------------------------
def index_public(batch: int = 4000) -> dict[str, Any]:
    """Embeds every public chunk with the deployed retriever's embedder (COPY into a temp table + one UPDATE)."""
    ret, tag = retriever()
    t0 = time.perf_counter()
    with control_session(owner=True) as db:
        rows = db.execute(select(PublicChunk.id, PublicChunk.content).order_by(PublicChunk.id)).all()
        conn = db.connection().connection.driver_connection
        assert conn is not None
        with conn.cursor() as cur:
            cur.execute("CREATE TEMP TABLE tmp_emb (id uuid PRIMARY KEY, embedding vector(256)) ON COMMIT DROP")
            for i in range(0, len(rows), batch):
                part = rows[i:i + batch]
                E = ret.embed([r[1] for r in part])
                buf = io.StringIO()
                for (cid, _), v in zip(part, E, strict=True):
                    buf.write(f"{cid}\t[{','.join(f'{x:.6f}' for x in v)}]\n")
                with cur.copy("COPY tmp_emb (id, embedding) FROM STDIN") as cp:
                    cp.write(buf.getvalue())
            cur.execute("UPDATE platform.public_chunks c SET embedding = t.embedding FROM tmp_emb t WHERE c.id = t.id")
            updated = cur.rowcount
        db.execute(text("ANALYZE platform.public_chunks"))
    return {"retriever": tag, "chunks": len(rows), "updated": updated, "seconds": round(time.perf_counter() - t0, 1)}


def chunk_text(text_: str, size: int = 900, overlap: int = 120) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text_) if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if len(cur) + len(p) + 1 <= size:
            cur = f"{cur}\n{p}".strip()
            continue
        if cur:
            chunks.append(cur)
        while len(p) > size:
            chunks.append(p[:size])
            p = p[size - overlap:]
        cur = p
    if cur:
        chunks.append(cur)
    return chunks


def index_kb_document(db: Session, doc: Document, pages: list[tuple[int, str]]) -> int:
    ret, _ = retriever()
    n = 0
    rows = []
    for page_no, t in pages:
        for c in chunk_text(t):
            rows.append((page_no, c))
    E = ret.embed([c for _, c in rows]) if rows else np.zeros((0, 256))
    for i, ((page_no, c), v) in enumerate(zip(rows, E, strict=True)):
        db.add(KbChunk(org_id=doc.org_id, document_id=doc.id, page_no=page_no, ordinal=i, kind="text", content=c, embedding=v.tolist()))
        n += 1
    db.flush()
    return n


def reembed_kb(db: Session) -> int:
    """Re-embeds the organisation's KB chunks after a retriever upgrade (RLS-scoped session)."""
    ret, _ = retriever()
    chunks = db.execute(select(KbChunk.id, KbChunk.content)).all()
    if not chunks:
        return 0
    E = ret.embed([c[1] for c in chunks])
    for (cid, _), v in zip(chunks, E, strict=True):
        db.execute(update(KbChunk).where(KbChunk.id == cid).values(embedding=v.tolist()))
    return len(chunks)
