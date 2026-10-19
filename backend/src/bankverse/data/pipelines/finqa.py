"""FinQA (MIT): 10-K report pages with expert numerical Q&A → bronze → silver facts/pages/questions →
public corpus (platform.public_documents / public_chunks) used by the RAG copilot.

Facts follow FinQA's own indexing (``text_i`` over pre_text + post_text, ``table_i`` over table rows) and
its table-row linearisation, so retrieval can be evaluated against the expert ``gold_inds`` annotations."""

from __future__ import annotations

import json
import re
from typing import Any

import polars as pl
from sqlalchemy import delete, func, select, text

from bankverse.data import lake, quality
from bankverse.data.registry import SOURCES_BY_KEY
from bankverse.data.runner import RunContext
from bankverse.db.models.platform import PublicChunk, PublicDocument
from bankverse.db.session import control_session

SOURCE = "finqa"
REF_RX = re.compile(r"^(?P<company>[A-Z.\-]+)/(?P<year>\d{4})/page_(?P<page>\d+)\.pdf$")


def linearize_row(header: list[str], row: list[str]) -> str:
    """FinQA linearisation: '<h0> the <row name> of <h_j> is <v_j> ;' for every value cell."""
    if not row:
        return ""
    name = row[0].strip()
    parts = [f"the {name} of {header[j].strip() if j < len(header) else ''} is {row[j].strip()} ;" for j in range(1, len(row))
             if row[j].strip()]
    return (f"{header[0].strip()} " if header and header[0].strip() else "") + " ".join(parts)


def page_facts(ex: dict[str, Any]) -> list[dict[str, Any]]:
    facts = []
    texts = list(ex["pre_text"]) + list(ex["post_text"])
    n_pre = len(ex["pre_text"])
    for i, t in enumerate(texts):
        t = t.strip()
        if t and t != ".":
            facts.append({"ind": f"text_{i}", "kind": "text", "ordinal": i, "content": t, "section": "pre" if i < n_pre else "post"})
    table = ex["table"]
    header = table[0] if table else []
    for i, row in enumerate(table):
        content = " | ".join(c.strip() for c in row if c.strip()) if i == 0 else linearize_row(header, row)
        if content.strip():
            facts.append({"ind": f"table_{i}", "kind": "table_row" if i else "table_header", "ordinal": 1000 + i, "content": content,
                          "section": "table"})
    return facts


def run(ctx: RunContext) -> dict[str, Any]:
    raw = lake.layer_dir("raw", SOURCE)
    exp = SOURCES_BY_KEY[SOURCE].expected
    with ctx.step("bronze") as st:
        rows = []
        for split in ("train", "dev", "test"):
            for ex in json.loads((raw / f"{split}.json").read_text()):
                rows.append({"split": split, "qid": ex["id"], "page_ref": ex["filename"], "record": json.dumps(ex, ensure_ascii=False)})
        bronze = pl.DataFrame(rows)
        b = lake.write_parquet(bronze, "bronze", "finqa_records")
        st["rows_out"] = bronze.height
    raw_id = lake.current_version_id(SOURCE, "raw")
    vb = lake.register_version(name="finqa_records", layer="bronze", path=b, source_key=SOURCE, rows=bronze.height, columns=bronze.width,
                               schema=lake.schema_of(bronze), run_id=ctx.run_id, upstream_ids=[raw_id] if raw_id else [],
                               transform="json → parquet (one row per QA example)")
    with ctx.step("silver") as st:
        pages: dict[str, dict[str, Any]] = {}
        facts: list[dict[str, Any]] = []
        qa: list[dict[str, Any]] = []
        conflicts = 0
        for r in bronze.iter_rows(named=True):
            ex = json.loads(r["record"])
            ref = r["page_ref"]
            m = REF_RX.match(ref)
            pf = page_facts(ex)
            if ref not in pages:
                pages[ref] = {"page_ref": ref, "company": m.group("company") if m else ref.split("/")[0],
                              "fiscal_year": int(m.group("year")) if m else None, "page": int(m.group("page")) if m else None,
                              "first_split": r["split"], "n_facts": len(pf), "n_text": sum(f["kind"] == "text" for f in pf),
                              "n_table_rows": sum(f["kind"] != "text" for f in pf)}
                facts.extend({"page_ref": ref, **f} for f in pf)
            elif pages[ref]["n_facts"] != len(pf):
                conflicts += 1
            q = ex["qa"]
            qa.append({"qid": ex["id"], "split": r["split"], "page_ref": ref, "question": q["question"], "answer": str(q.get("answer", "")),
                       "program": q.get("program", ""), "gold_inds": list(q.get("gold_inds", {}).keys()),
                       "exe_ans": float(q["exe_ans"]) if isinstance(q.get("exe_ans"), (int, float)) else None,
                       "exe_ans_text": str(q.get("exe_ans"))})
        pages_df = pl.DataFrame(list(pages.values()))
        facts_df = pl.DataFrame(facts)
        qa_df = pl.DataFrame(qa)
        counts = qa_df.group_by("split").len()
        observed = dict(zip(counts.get_column("split").to_list(), counts.get_column("len").to_list(), strict=True))
        checks = [quality.CheckResult(f"{s}_examples", "pass" if observed.get(s) == exp[s] else "fail", observed=observed.get(s), threshold=exp[s])
                  for s in ("train", "dev", "test")]
        checks += [quality.unique(pages_df, ["page_ref"]), quality.unique(facts_df, ["page_ref", "ind"]),
                   *quality.not_null(qa_df, ["question", "page_ref"]),
                   quality.CheckResult("page_content_conflicts", "pass" if conflicts == 0 else "warn", observed=conflicts, threshold=0, blocking=False)]
        p1 = lake.write_parquet(pages_df, "silver", "finqa_pages")
        p2 = lake.write_parquet(facts_df, "silver", "finqa_facts")
        p3 = lake.write_parquet(qa_df, "silver", "finqa_questions")
        st["rows_out"] = facts_df.height
    ids = []
    for name, path, df, tr in (("finqa_pages", p1, pages_df, "dedupe pages, parse company/year/page"),
                               ("finqa_facts", p2, facts_df, "FinQA fact indexing + table-row linearisation"),
                               ("finqa_questions", p3, qa_df, "QA with gold facts and programs")):
        vid = lake.register_version(name=name, layer="silver", path=path, source_key=SOURCE, rows=df.height, columns=df.width,
                                    schema=lake.schema_of(df), prof=lake.profile(df), run_id=ctx.run_id, upstream_ids=[vb], transform=tr)
        lake.set_quality_status(vid, quality.persist(vid, checks))
        ids.append(vid)
    quality.enforce(checks)
    with ctx.step("public_corpus") as st:
        st["rows_out"] = load_public_corpus(pages_df, facts_df)
    return {"pages": pages_df.height, "facts": facts_df.height, "questions": qa_df.height, "companies": pages_df.get_column("company").n_unique()}


def load_public_corpus(pages_df: pl.DataFrame, facts_df: pl.DataFrame) -> int:
    """(Re)loads the public report corpus. Embeddings are filled later by ``bankverse copilot-index``
    with the deployed retriever's embedder, so the stored vectors always match the serving model."""
    by_page: dict[str, list[dict[str, Any]]] = {}
    for f in facts_df.iter_rows(named=True):
        by_page.setdefault(f["page_ref"], []).append(f)
    n = 0
    with control_session(owner=True) as db:
        db.execute(delete(PublicDocument).where(PublicDocument.source_key == SOURCE))
        db.flush()
        for p in pages_df.iter_rows(named=True):
            doc = PublicDocument(source_key=SOURCE, doc_ref=p["page_ref"], company=p["company"], fiscal_year=p["fiscal_year"], page=p["page"],
                                 title=f"{p['company']} — rapport annuel {p['fiscal_year']} (10-K), page {p['page']}")
            db.add(doc)
            db.flush()
            chunks = [PublicChunk(document_id=doc.id, ordinal=f["ordinal"], kind=f["kind"], content=f["content"],
                                  meta={"ind": f["ind"], "section": f["section"]}) for f in by_page.get(p["page_ref"], [])]
            db.add_all(chunks)
            n += len(chunks)
        db.flush()
        # Fresh statistics: the planner otherwise assumes empty tables (nested-loop joins on 80k+ rows).
        db.execute(text("ANALYZE platform.public_documents"))
        db.execute(text("ANALYZE platform.public_chunks"))
        total = db.execute(select(func.count()).select_from(PublicChunk).join(PublicDocument)
                           .where(PublicDocument.source_key == SOURCE)).scalar_one()
        if total != n:
            raise RuntimeError(f"public corpus load mismatch: {total} != {n}")
    return n
