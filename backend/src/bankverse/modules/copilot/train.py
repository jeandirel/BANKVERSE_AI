"""Copilot retriever: BM25 + TF-IDF + LSA embeddings + learned reranker (LightGBM LambdaRank), trained on FinQA
train questions with the experts' gold supporting facts, early-stopped on dev, evaluated on test.

Evaluation
* In-page fact retrieval (FinQA's retriever task): recall@3 / recall@5 of gold facts, all-gold@3, MRR.
* Open-corpus page retrieval over 2,789 report pages: hit@k with no filter and with a company filter
  (the UI lets the analyst scope a question to a company, as an analyst would).
* Calculator: execution of the experts' gold programs (checks the calculator, not answer generation).
Answer *generation* is not evaluated here: without an LLM provider the copilot is extractive."""

from __future__ import annotations

import contextlib
import time
from collections import defaultdict
from typing import Any

import joblib
import lightgbm as lgb
import numpy as np
import polars as pl
import scipy.sparse as sp
import structlog

from bankverse.data import lake
from bankverse.db.session import control_session
from bankverse.ml import registry, tracking
from bankverse.modules.copilot import calculator
from bankverse.modules.copilot.retrieval import (
    BM25,
    FEATURES,
    Retriever,
    build_embedder,
    build_tfidf,
    rrf,
)

log = structlog.get_logger()
MODEL_NAME = "copilot_retriever"
SEED = 42


def _load() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    facts = lake.read_dataset("silver", "finqa_facts", lazy=False)
    qa = lake.read_dataset("silver", "finqa_questions", lazy=False)
    pages = lake.read_dataset("silver", "finqa_pages", lazy=False)
    assert isinstance(facts, pl.DataFrame) and isinstance(qa, pl.DataFrame) and isinstance(pages, pl.DataFrame)
    return facts, qa, pages


def _candidates(facts: pl.DataFrame) -> dict[str, list[dict[str, Any]]]:
    by_page: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for i, f in enumerate(facts.iter_rows(named=True)):
        by_page[f["page_ref"]].append({"row": i, "ind": f["ind"], "content": f["content"], "kind": f["kind"]})
    for lst in by_page.values():
        n = len(lst)
        for j, f in enumerate(lst):
            f["rel_pos"] = j / max(n - 1, 1)
    return by_page


def _feature_set(ret: Retriever, qa: pl.DataFrame, by_page: dict[str, list[dict[str, Any]]], E: np.ndarray
                 ) -> tuple[np.ndarray, np.ndarray, list[int], list[list[str]], list[list[str]]]:
    X, y, groups, inds, golds = [], [], [], [], []
    for r in qa.iter_rows(named=True):
        cands = by_page.get(r["page_ref"], [])
        if not cands:
            continue
        rows = [c["row"] for c in cands]
        X.append(ret.features(r["question"], cands, E[rows]))
        gold = set(r["gold_inds"])
        y.append(np.array([1 if c["ind"] in gold else 0 for c in cands], dtype=np.int32))
        groups.append(len(cands))
        inds.append([c["ind"] for c in cands])
        golds.append(list(gold))
    return np.vstack(X), np.concatenate(y), groups, inds, golds


def _rank_metrics(scores: np.ndarray, groups: list[int], inds: list[list[str]], golds: list[list[str]]) -> dict[str, float]:
    r3, r5, all3, mrr = [], [], [], []
    off = 0
    for g, ind, gold in zip(groups, inds, golds, strict=True):
        s = scores[off: off + g]
        off += g
        if not gold:
            continue
        order = [ind[i] for i in np.argsort(-s, kind="stable")]
        gs = set(gold)
        r3.append(len(gs & set(order[:3])) / len(gs))
        r5.append(len(gs & set(order[:5])) / len(gs))
        all3.append(float(gs <= set(order[:3])))
        first = next((k for k, x in enumerate(order) if x in gs), None)
        mrr.append(0.0 if first is None else 1.0 / (first + 1))
    return {"recall@3": round(float(np.mean(r3)), 4), "recall@5": round(float(np.mean(r5)), 4),
            "all_gold@3": round(float(np.mean(all3)), 4), "mrr": round(float(np.mean(mrr)), 4), "questions": len(r3)}


def _page_retrieval(ret: Retriever, facts: pl.DataFrame, pages: pl.DataFrame, qa: pl.DataFrame, E: np.ndarray) -> dict[str, Any]:
    page_refs = pages.get_column("page_ref").to_list()
    companies = pages.get_column("company").to_list()
    idx = {p: i for i, p in enumerate(page_refs)}
    page_of_fact = np.array([idx[p] for p in facts.get_column("page_ref").to_list()])
    texts: dict[int, list[str]] = defaultdict(list)
    for p, c in zip(page_of_fact, facts.get_column("content").to_list(), strict=True):
        texts[int(p)].append(c)
    page_docs = [" ".join(texts[i]) for i in range(len(page_refs))]
    P = ret.bm25.doc_matrix(page_docs)
    # page embedding = normalised mean of its fact embeddings
    M = sp.csr_matrix((np.ones(len(page_of_fact)), (page_of_fact, np.arange(len(page_of_fact)))), shape=(len(page_refs), len(page_of_fact)))
    PE = np.asarray(M @ E)
    PE /= np.linalg.norm(PE, axis=1, keepdims=True) + 1e-9
    Q = qa.get_column("question").to_list()
    target = np.array([idx[p] for p in qa.get_column("page_ref").to_list()])
    QB = sp.vstack([ret.bm25.query_vector(q) for q in Q])
    bm = (QB @ P.T).toarray()
    de = ret.embed(Q) @ PE.T
    comp = np.array(companies)
    out: dict[str, Any] = {}
    for name, S in (("bm25", bm), ("lsa", de), ("hybrid_rrf", None)):
        hits = {1: [], 5: [], 20: []}
        hits_c = {1: [], 5: []}
        for i in range(len(Q)):
            s = S[i] if S is not None else rrf([bm[i], de[i]])
            order = np.argsort(-s)
            pos = int(np.where(order == target[i])[0][0])
            for k in hits:
                hits[k].append(pos < k)
            mask = comp == comp[target[i]]
            sub = np.where(mask)[0]
            order_c = sub[np.argsort(-s[sub])]
            pos_c = int(np.where(order_c == target[i])[0][0])
            for k in hits_c:
                hits_c[k].append(pos_c < k)
        out[name] = {**{f"hit@{k}": round(float(np.mean(v)), 4) for k, v in hits.items()},
                     **{f"company_filter_hit@{k}": round(float(np.mean(v)), 4) for k, v in hits_c.items()}}
    out["pages"] = len(page_refs)
    out["mean_pages_per_company"] = round(len(page_refs) / len(set(companies)), 1)
    return out


def train(*, trained_by: str = "pipeline", quick: bool = False) -> dict[str, Any]:
    t0 = time.perf_counter()
    facts, qa, pages = _load()
    texts = facts.get_column("content").to_list()
    log.info("copilot_fit_indexes", facts=len(texts))
    ret = Retriever(bm25=BM25().fit(texts), tfidf=build_tfidf(texts), embedder=build_embedder(texts, SEED), reranker=None)
    E = ret.embed(texts)
    by_page = _candidates(facts)
    splits = {s: qa.filter(pl.col("split") == s) for s in ("train", "dev", "test")}
    if quick:
        splits = {s: d.head(300) for s, d in splits.items()}
    feats = {s: _feature_set(ret, d, by_page, E) for s, d in splits.items()}
    log.info("copilot_features_done", **{s: len(f[1]) for s, f in feats.items()})
    Xtr, ytr, gtr, _, _ = feats["train"]
    Xdv, ydv, gdv, _, _ = feats["dev"]
    ranker = lgb.LGBMRanker(objective="lambdarank", n_estimators=600, learning_rate=0.05, num_leaves=31, min_child_samples=20,
                            subsample=0.8, subsample_freq=1, colsample_bytree=0.9, random_state=SEED, verbose=-1)
    ranker.fit(Xtr, ytr, group=gtr, eval_set=[(Xdv, ydv)], eval_group=[gdv], eval_at=[3],
               callbacks=[lgb.early_stopping(50, verbose=False)])
    comparison = {}
    for split in ("dev", "test"):
        X, _, g, ind, gold = feats[split]
        col = {n: i for i, n in enumerate(FEATURES)}
        comparison[split] = {
            "bm25": _rank_metrics(X[:, col["bm25"]], g, ind, gold),
            "tfidf_cosine": _rank_metrics(X[:, col["tfidf_cos"]], g, ind, gold),
            "lsa_embedding": _rank_metrics(X[:, col["lsa_cos"]], g, ind, gold),
            "hybrid_rrf": _rank_metrics(np.concatenate([rrf([X[o:o + n, col["bm25"]], X[o:o + n, col["lsa_cos"]]])
                                                        for o, n in zip(np.cumsum([0, *g[:-1]]), g, strict=True)]), g, ind, gold),
            "reranker_lambdarank": _rank_metrics(ranker.predict(X), g, ind, gold),
        }
    ret.reranker = ranker
    importances = sorted(zip(FEATURES, ranker.booster_.feature_importance("gain").tolist(), strict=True), key=lambda kv: -kv[1])
    page_ret = _page_retrieval(ret, facts, pages, splits["test"], E)
    # calculator check on gold programs (needs raw tables)
    calc = _calculator_check()
    metrics = {
        "fact_retrieval": comparison,
        "page_retrieval_test": page_ret,
        "calculator_gold_programs": calc,
        "reranker": {"best_iteration": int(ranker.best_iteration_ or ranker.n_estimators),
                     "feature_importance_gain": [{"feature": f, "gain": round(v, 1)} for f, v in importances]},
        "corpus": {"pages": pages.height, "facts": facts.height, "companies": pages.get_column("company").n_unique(),
                   "questions": {s: d.height for s, d in splits.items()}},
    }
    card = {"model": MODEL_NAME,
            "intended_use": "Retrieve cited evidence (sentences and table rows) from public annual-report pages to support analysts' questions.",
            "training_data": "FinQA (MIT) — 10-K report pages of S&P 500 companies with expert questions, gold supporting facts and programs.",
            "limitations": ["English financial reports only; French questions retrieve poorly (lexical + LSA models are English-trained).",
                            "Extractive by default: answer synthesis requires a configured LLM provider (Bedrock / Anthropic).",
                            "Open-corpus page retrieval without a company filter is weak (FinQA questions rarely name the company).",
                            "Evidence is from public filings dated 1999–2019; it is not current market information or advice."]}
    with control_session() as db:
        version = registry.next_version(db, MODEL_NAME)
    adir = registry.artifact_dir(MODEL_NAME, version)
    artifact = adir / "model.joblib"
    joblib.dump(ret, artifact, compress=3)
    flat = {"test_recall3_reranker": comparison["test"]["reranker_lambdarank"]["recall@3"],
            "test_recall3_bm25": comparison["test"]["bm25"]["recall@3"],
            "test_page_hit5_company": page_ret["hybrid_rrf"]["company_filter_hit@5"], "calc_exec_acc": calc["test"]["accuracy"]}
    with tracking.run("copilot_retriever", f"v{version}", tags={"module": "copilot"}) as run_id:
        tracking.log_metrics(flat)
    ids = [str(v) for v in (lake.current_version_id("finqa_facts", "silver"), lake.current_version_id("finqa_questions", "silver")) if v]
    with control_session(owner=True) as db:
        registry.register_version(db, name=MODEL_NAME, module="copilot", task="evidence retrieval (learning to rank)",
                                  description="Hybrid BM25 + LSA retrieval with a LambdaRank reranker over FinQA report pages.",
                                  version=version, algorithm="bm25 + lsa + lightgbm lambdarank", artifact_path=artifact, metrics=metrics,
                                  params={"embed_dim": 256, "reranker_trees": int(ranker.best_iteration_ or 0)}, features=FEATURES,
                                  dataset_versions=ids, reference_profile={}, model_card=card, mlflow_run_id=run_id, trained_by=trained_by,
                                  training_seconds=time.perf_counter() - t0, risk_tier="limited")
    log.info("copilot_training_done", version=version, **flat)
    return {"model": MODEL_NAME, "version": version, **flat}


def _calculator_check() -> dict[str, Any]:
    import json

    raw = lake.layer_dir("raw", "finqa")
    out = {}
    for split in ("dev", "test"):
        data = json.loads((raw / f"{split}.json").read_text())
        ok = 0
        for ex in data:
            with contextlib.suppress(calculator.CalcError):
                ok += calculator.matches(calculator.run_program(ex["qa"]["program"], ex["table"]), ex["qa"]["exe_ans"])
        out[split] = {"n": len(data), "accuracy": round(ok / len(data), 4)}
    return out
