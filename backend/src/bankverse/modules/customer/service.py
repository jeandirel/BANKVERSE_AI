"""Customer intelligence services: message topics & semantic search (pgvector), behavioural segmentation.

Segmentation is fitted on each bank's *own* customers (unsupervised; nothing transferred from public
data): K-Means with k chosen by silhouette (3–7) compared with DBSCAN; segments are named from their
centroid profile with transparent rules.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

import numpy as np
import polars as pl
from sklearn.cluster import DBSCAN, KMeans
from sklearn.metrics import silhouette_score
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.orm import Session

from bankverse.db.models.tenant import (
    Account,
    CardTransaction,
    Customer,
    CustomerMessage,
    CustomerSegmentAssignment,
    Transfer,
)
from bankverse.ml import registry
from bankverse.ml.dl import Standardizer
from bankverse.modules.customer.train import TOPIC_MODEL, intent_group

SEG_FEATURES = ["tx_per_month", "spend_per_month", "avg_ticket", "online_share", "foreign_share", "distinct_mcc",
                "income_k", "age", "transfers_in_per_month", "balance_k"]
SEG_LABELS = {
    "tx_per_month": ("très actifs", "peu actifs"), "online_share": ("digitaux", "en magasin"), "income_k": ("aisés", "revenus modestes"),
    "age": ("seniors", "jeunes"), "foreign_share": ("voyageurs", "domestiques"), "avg_ticket": ("gros paniers", "petits paniers"),
}


def classify_messages(db: Session, org_id: uuid.UUID, only_missing: bool = False) -> int:
    loaded = registry.load_production(TOPIC_MODEL)
    clf, emb = loaded.payload["classifier"], loaded.payload["embedder"]
    complaints = set(loaded.payload["complaint_intents"])
    q = select(CustomerMessage.id, CustomerMessage.text)
    if only_missing:
        q = q.where(CustomerMessage.predicted_topic.is_(None))
    rows = db.execute(q).all()
    if not rows:
        return 0
    texts = [r.text for r in rows]
    proba = clf.predict_proba(texts)
    classes = clf.classes_
    vecs = emb.transform(texts)
    for (mid, _), p, v in zip(rows, proba, vecs, strict=True):
        j = int(np.argmax(p))
        intent = str(classes[j])
        db.execute(update(CustomerMessage).where(CustomerMessage.id == mid).values(
            predicted_topic=intent, topic_group=intent_group(intent), confidence=float(p[j]), model_version=loaded.tag,
            is_complaint=intent in complaints, embedding=v.astype(np.float32).tolist()))
    return len(rows)


def semantic_search(db: Session, query: str, limit: int = 10) -> list[dict[str, Any]]:
    loaded = registry.load_production(TOPIC_MODEL)
    v = loaded.payload["embedder"].transform([query])[0].astype(np.float32).tolist()
    rows = db.execute(
        select(CustomerMessage, Customer.full_name, CustomerMessage.embedding.cosine_distance(v).label("d"))
        .outerjoin(Customer, Customer.id == CustomerMessage.customer_id)
        .where(CustomerMessage.embedding.isnot(None)).order_by("d").limit(limit)).all()
    return [{"id": str(m.id), "text": m.text, "customer": name, "topic": m.predicted_topic, "group": m.topic_group,
             "received_at": m.received_at, "similarity": round(1 - float(d), 4)} for m, name, d in rows]


def customer_features(db: Session) -> pl.DataFrame:
    span_days = db.execute(text("SELECT GREATEST(EXTRACT(EPOCH FROM max(ts) - min(ts)) / 86400, 1) FROM bank.card_transactions")).scalar() or 1
    months = float(span_days) / 30.0
    tx = db.execute(select(CardTransaction.customer_id, func.count(), func.sum(func.abs(CardTransaction.amount)),
                           func.avg(func.abs(CardTransaction.amount)),
                           func.avg((CardTransaction.use_chip == "Online Transaction").cast(Integer_())),
                           func.count(func.distinct(CardTransaction.mcc)))
                    .group_by(CardTransaction.customer_id)).all()
    foreign = dict(db.execute(select(CardTransaction.customer_id, func.avg(
        (CardTransaction.merchant_state.notin_(list(_fr_regions())) & (CardTransaction.merchant_city != "ONLINE")).cast(Integer_())))
        .group_by(CardTransaction.customer_id)).all())
    tin = dict(db.execute(select(Account.customer_id, func.count()).join(Transfer, Transfer.dst_account_id == Account.id)
                          .group_by(Account.customer_id)).all())
    bal = dict(db.execute(select(Account.customer_id, func.sum(Account.balance)).group_by(Account.customer_id)).all())
    custs = {c.id: c for c in db.execute(select(Customer)).scalars()}
    rows = []
    for cid, n, spend, ticket, online, mccs in tx:
        c = custs.get(cid)
        if c is None:
            continue
        rows.append({"customer_id": str(cid), "tx_per_month": n / months, "spend_per_month": float(spend or 0) / months,
                     "avg_ticket": float(ticket or 0), "online_share": float(online or 0), "foreign_share": float(foreign.get(cid) or 0),
                     "distinct_mcc": float(mccs), "income_k": float(c.annual_income or 0) / 1000,
                     "age": float(2026 - c.birth_year) if c.birth_year else 45.0, "transfers_in_per_month": tin.get(cid, 0) / months,
                     "balance_k": float(bal.get(cid) or 0) / 1000})
    return pl.DataFrame(rows)


def Integer_():  # noqa: N802 - small helper to keep SQL casts readable
    from sqlalchemy import Integer

    return Integer


def _fr_regions() -> set[str]:
    from bankverse.modules.fraud.service import FR_REGIONS

    return FR_REGIONS


def segment(db: Session, org_id: uuid.UUID, seed: int = 7) -> dict[str, Any]:
    df = customer_features(db)
    if df.height < 50:
        return {"status": "insufficient_customers", "n": df.height}
    X_raw = df.select(SEG_FEATURES).to_numpy().astype(np.float32)
    X_log = np.log1p(np.clip(X_raw, 0, None))
    scaler = Standardizer().fit(X_log)
    X = scaler.transform(X_log)
    scores = {}
    for k in range(3, 8):
        km = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(X)
        scores[k] = float(silhouette_score(X, km.labels_, sample_size=min(2000, len(X)), random_state=seed))
    k_best = max(scores, key=scores.get)  # type: ignore[arg-type]
    km = KMeans(n_clusters=k_best, n_init=10, random_state=seed).fit(X)
    db_labels = DBSCAN(eps=1.6, min_samples=15).fit_predict(X)
    n_noise = int((db_labels == -1).sum())
    db_clusters = len(set(db_labels)) - (1 if -1 in db_labels else 0)
    db_sil = float(silhouette_score(X[db_labels != -1], db_labels[db_labels != -1])) if db_clusters >= 2 else None
    centers = km.cluster_centers_
    segs = []
    for c in range(k_best):
        z = centers[c]
        top = np.argsort(-np.abs(z))[:2]
        words = []
        for j in top:
            f = SEG_FEATURES[j]
            if f in SEG_LABELS:
                words.append(SEG_LABELS[f][0] if z[j] > 0 else SEG_LABELS[f][1])
        label = "Clients " + " & ".join(words) if words else f"Segment {c + 1}"
        members = km.labels_ == c
        segs.append({"segment_id": c, "label": label, "size": int(members.sum()),
                     "profile": {f: round(float(np.median(X_raw[members, j])), 2) for j, f in enumerate(SEG_FEATURES)}})
    version = f"kmeans-k{k_best}-{datetime.now(UTC):%Y%m%d%H%M}"
    db.execute(delete(CustomerSegmentAssignment))
    ids = df.get_column("customer_id").to_list()
    rows = [{"id": uuid.uuid4(), "org_id": org_id, "customer_id": uuid.UUID(cid), "model_version": version,
             "segment_id": int(km.labels_[i]), "segment_label": segs[int(km.labels_[i])]["label"],
             "features": {f: round(float(X_raw[i, j]), 3) for j, f in enumerate(SEG_FEATURES)}} for i, cid in enumerate(ids)]
    for k in range(0, len(rows), 2000):
        db.execute(insert(CustomerSegmentAssignment), rows[k:k + 2000])
    return {"model_version": version, "k": k_best, "silhouette_by_k": {str(k): round(v, 4) for k, v in scores.items()},
            "segments": segs, "dbscan": {"clusters": db_clusters, "noise_points": n_noise,
                                         "silhouette": round(db_sil, 4) if db_sil is not None else None},
            "customers": len(ids)}


def batch_score_org(db: Session, org_id: uuid.UUID) -> dict[str, Any]:
    t0 = time.perf_counter()
    n = classify_messages(db, org_id)
    seg = segment(db, org_id)
    return {"messages_classified": n, "segmentation": {k: v for k, v in seg.items() if k != "segments"},
            "seconds": round(time.perf_counter() - t0, 1)}
