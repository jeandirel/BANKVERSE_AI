"""Retrieval primitives shared by training/evaluation and serving: tokenisation, BM25, LSA embeddings and
the feature set of the learned reranker. Pure numpy / scikit-learn (no network, no external model)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

import numpy as np
import scipy.sparse as sp
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import Normalizer

TOKEN = re.compile(r"[a-z]+|\d+(?:\.\d+)?")
NUMBER = re.compile(r"(?<![\w.])-?\$?\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s?%?")
YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
STOP = frozenset(["a", "an", "the", "of", "in", "on", "for", "to", "and", "or", "is", "are", "was", "were", "be", "been", "by", "with", "as", "at", "from", "that", "this", "these", "those", "what", "which", "how", "much", "many", "did", "does", "do", "was", "were", "its", "it", "their", "there", "than", "then", "into", "over", "under", "per", "about", "between", "during", "after", "before", "total", "change", "percent", "percentage", "portion", "ratio", "value"])

EMBED_DIM = 256
FEATURES = ["bm25", "bm25_norm", "bm25_rank", "tfidf_cos", "lsa_cos", "num_overlap", "num_overlap_frac", "year_overlap",
            "year_in_question", "q_coverage", "is_table_row", "is_header", "is_text", "log_len", "rel_pos", "n_numbers",
            "lsa_rank", "tfidf_rank"]


def tokens(s: str) -> list[str]:
    return TOKEN.findall(s.lower())


def content_terms(s: str) -> set[str]:
    return {t for t in tokens(s) if t not in STOP and len(t) > 1}


def numbers(s: str) -> set[str]:
    out = set()
    for m in NUMBER.finditer(s.replace("$ ", "$")):
        whole = m.group(1).replace(",", "")
        frac = m.group(2)
        v = float(f"{whole}.{frac}" if frac else whole)
        out.add(f"{v:g}")
    return out


def years(s: str) -> set[str]:
    return set(YEAR.findall(s))


class BM25:
    """Okapi BM25 over a fixed vocabulary (idf from the indexing corpus)."""

    def __init__(self, k1: float = 1.2, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.vec = CountVectorizer(tokenizer=tokens, lowercase=False, token_pattern=None, min_df=1)

    def fit(self, docs: list[str]) -> BM25:
        tf = self.vec.fit_transform(docs)
        n = tf.shape[0]
        df = np.bincount(tf.indices, minlength=tf.shape[1])
        self.idf = np.log(1 + (n - df + 0.5) / (df + 0.5)).astype(np.float32)
        self.avgdl = float(tf.sum() / max(n, 1))
        return self

    def doc_matrix(self, docs: list[str]) -> sp.csr_matrix:
        tf = self.vec.transform(docs).astype(np.float32).tocsr()
        dl = np.asarray(tf.sum(axis=1)).ravel()
        rows = np.repeat(np.arange(tf.shape[0]), np.diff(tf.indptr))
        denom = tf.data + self.k1 * (1 - self.b + self.b * dl[rows] / self.avgdl)
        tf.data = (tf.data * (self.k1 + 1) / denom) * self.idf[tf.indices]
        return tf

    def query_vector(self, q: str) -> sp.csr_matrix:
        qv = self.vec.transform([q])
        qv.data[:] = 1.0
        return qv.astype(np.float32)


def build_embedder(texts: list[str], seed: int = 42) -> Pipeline:
    emb = Pipeline([("tfidf", TfidfVectorizer(tokenizer=tokens, lowercase=False, token_pattern=None, ngram_range=(1, 2), min_df=2,
                                              sublinear_tf=True, max_features=200_000)),
                    ("svd", TruncatedSVD(n_components=EMBED_DIM, random_state=seed)), ("norm", Normalizer())])
    emb.fit(texts)
    return emb


def build_tfidf(texts: list[str]) -> TfidfVectorizer:
    return TfidfVectorizer(tokenizer=tokens, lowercase=False, token_pattern=None, ngram_range=(1, 2), min_df=2, sublinear_tf=True,
                           max_features=300_000).fit(texts)


@dataclass
class Retriever:
    """Bundle persisted as the copilot model artifact."""

    bm25: BM25
    tfidf: TfidfVectorizer
    embedder: Pipeline
    reranker: Any | None
    version_note: str = ""

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self.embedder.transform(texts), dtype=np.float32)

    def features(self, question: str, facts: list[dict[str, Any]], fact_emb: np.ndarray | None = None) -> np.ndarray:
        """Feature matrix for candidate facts of ONE question. facts: dicts with content, kind, ordinal_rel."""
        texts = [f["content"] for f in facts]
        n = len(texts)
        if n == 0:
            return np.zeros((0, len(FEATURES)), dtype=np.float32)
        D = self.bm25.doc_matrix(texts)
        bm = np.asarray(D @ self.bm25.query_vector(question).T.toarray()).ravel()
        T = self.tfidf.transform(texts)
        tq = self.tfidf.transform([question])
        tcos = np.asarray((T @ tq.T).toarray()).ravel()
        E = fact_emb if fact_emb is not None else self.embed(texts)
        qe = self.embed([question])[0]
        lcos = E @ qe
        qnums, qyears, qterms = numbers(question), years(question), content_terms(question)
        rows = []
        bm_rank = _rank(bm)
        l_rank = _rank(lcos)
        t_rank = _rank(tcos)
        mx = bm.max() if bm.max() > 0 else 1.0
        for i, f in enumerate(facts):
            c = f["content"]
            fn = numbers(c)
            fy = years(c)
            ov = len(qnums & fn)
            rows.append([bm[i], bm[i] / mx, bm_rank[i] / n, tcos[i], lcos[i], ov, ov / max(len(qnums), 1),
                         len(qyears & fy), float(len(qyears) > 0), len(qterms & content_terms(c)) / max(len(qterms), 1),
                         float(f["kind"] == "table_row"), float(f["kind"] == "table_header"), float(f["kind"] == "text"),
                         math.log1p(len(c.split())), f.get("rel_pos", 0.0), min(len(fn), 20), l_rank[i] / n, t_rank[i] / n])
        return np.asarray(rows, dtype=np.float32)

    def rank(self, question: str, facts: list[dict[str, Any]], fact_emb: np.ndarray | None = None) -> np.ndarray:
        """Scores (higher = more relevant). Falls back to BM25 + LSA fusion without a reranker."""
        X = self.features(question, facts, fact_emb)
        if len(X) == 0:
            return np.zeros(0)
        if self.reranker is not None:
            return np.asarray(self.reranker.predict(X), dtype=np.float64)
        return rrf([X[:, 0], X[:, 4]])


def _rank(v: np.ndarray) -> np.ndarray:
    """0 = best."""
    order = np.argsort(-v, kind="stable")
    r = np.empty_like(order)
    r[order] = np.arange(len(v))
    return r.astype(np.float32)


def rrf(score_lists: list[np.ndarray], k: int = 60) -> np.ndarray:
    """Reciprocal-rank fusion of several score vectors over the same candidates."""
    out = np.zeros(len(score_lists[0]))
    for s in score_lists:
        out += 1.0 / (k + 1 + _rank(np.asarray(s)))
    return out
