"""Document AI training & evaluation on a synthetic DEMO specimen corpus.

Protocol (no real identity or financial documents are used — none may be stored in the demo):
* 5 document types × 3 layouts; native PDFs plus degraded "phone scan" PNGs (OCR path).
* The classifier is trained on layouts 0–1 and evaluated on layout 2, never seen in training, so the
  score measures generalisation to a new issuer layout rather than memorisation of a template.
* Field extraction and consistency controls are evaluated against the generator's ground truth,
  separately for native PDFs and scans.
* Out-of-distribution rejection: groups of BANKING77 customer messages (not documents) must fall
  under the confidence threshold and be reported as "unrecognised".
Synthetic layouts are far more regular than real-world documents: these metrics are an upper bound
and do not transfer to production documents without a labelled real corpus."""

from __future__ import annotations

import random
import time
from collections import defaultdict
from typing import Any

import joblib
import numpy as np
import polars as pl
import structlog
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
from sklearn.pipeline import FeatureUnion, Pipeline

from bankverse.data import lake
from bankverse.db.session import control_session
from bankverse.ml import registry, tracking
from bankverse.modules.documents import process, synth

log = structlog.get_logger()
MODEL_NAME = "document_classifier"
REJECT_THRESHOLD = 0.55
TRAIN_TEMPLATES, TEST_TEMPLATES = (0, 1), (2,)


def _build_corpus(n_native: int, n_scan: int, seed: int = 7) -> list[dict[str, Any]]:
    rows = []
    s = seed
    for t in synth.DOC_TYPES:
        for tpl in (0, 1, 2):
            for i in range(n_native + n_scan):
                s += 1
                d = synth.generate(t, tpl, seed=s)
                scanned = i >= n_native
                if scanned:
                    data, mime = synth.to_scan(d.pdf, seed=s), "image/png"
                else:
                    data, mime = d.pdf, "application/pdf"
                pages = process.extract(data, mime)
                rows.append({"doc_type": t, "template": tpl, "scanned": scanned, "pages": pages,
                             "text": "\n".join(p.text for p in pages), "truth": d.truth, "defects": d.defects,
                             "ocr_conf": pages[0].ocr_conf})
        log.info("documents_corpus_type_done", doc_type=t, total=len(rows))
    return rows


def _same(name: str, extracted: str | None, truth: Any) -> bool:
    if extracted is None or truth is None:
        return False
    if isinstance(truth, (int, float)):
        try:
            return abs(float(extracted) - float(truth)) <= 0.011
        except ValueError:
            return False
    a = " ".join(str(extracted).split()).casefold().replace(" ", "")
    b = " ".join(str(truth).split()).casefold().replace(" ", "")
    return a == b


def _evaluate_extraction(rows: list[dict[str, Any]]) -> dict[str, Any]:
    per_field: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0])
    detection = {"native": {"tp": 0, "fp": 0, "fn": 0, "tn": 0}, "scan": {"tp": 0, "fp": 0, "fn": 0, "tn": 0}}
    for r in rows:
        mode = "scan" if r["scanned"] else "native"
        fields = process.extract_fields(r["doc_type"], r["pages"])
        for f in fields:
            if f["name"] not in r["truth"]:
                continue
            k = (r["doc_type"], f["name"], mode)
            per_field[k][1] += 1
            per_field[k][0] += int(_same(f["name"], f["normalized"], r["truth"][f["name"]]))
        checks = process.run_checks(r["doc_type"], fields, today=synth.REFERENCE_DATE)
        flagged = any(c["status"] == "fail" for c in checks)
        has_defect = bool(r["defects"])
        key = ("tp" if flagged else "fn") if has_defect else ("fp" if flagged else "tn")
        detection[mode][key] += 1
    fields_out: dict[str, Any] = {}
    agg = {"native": [0, 0], "scan": [0, 0]}
    for (t, name, mode), (ok, n) in sorted(per_field.items()):
        fields_out.setdefault(t, {}).setdefault(name, {})[mode] = round(ok / n, 3) if n else None
        agg[mode][0] += ok
        agg[mode][1] += n
    det = {}
    for mode, c in detection.items():
        det[mode] = {**c, "recall": round(c["tp"] / max(c["tp"] + c["fn"], 1), 3),
                     "precision": round(c["tp"] / max(c["tp"] + c["fp"], 1), 3),
                     "false_positive_rate": round(c["fp"] / max(c["fp"] + c["tn"], 1), 3)}
    return {"field_accuracy": {m: round(a[0] / a[1], 4) if a[1] else None for m, a in agg.items()},
            "field_accuracy_by_type": fields_out, "defect_detection": det}


def _ood_texts(n: int = 40) -> list[str]:
    try:
        df = lake.read_dataset("silver", "banking77", lazy=False)
        assert isinstance(df, pl.DataFrame)
        texts = df.get_column("text").to_list()
    except Exception:
        return []
    rng = random.Random(11)
    return ["\n".join(rng.sample(texts, 15)) for _ in range(n)]


def train(*, trained_by: str = "pipeline", quick: bool = False) -> dict[str, Any]:
    t0 = time.perf_counter()
    n_native, n_scan = (6, 2) if quick else (30, 8)
    rows = _build_corpus(n_native, n_scan)
    train_rows = [r for r in rows if r["template"] in TRAIN_TEMPLATES]
    test_rows = [r for r in rows if r["template"] in TEST_TEMPLATES]
    model = Pipeline([
        ("f", FeatureUnion([
            ("word", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, strip_accents="unicode", lowercase=True, min_df=2)),
            ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, lowercase=True, min_df=2)),
        ])),
        ("clf", LogisticRegression(C=5, max_iter=3000)),
    ])
    model.fit([r["text"] for r in train_rows], [r["doc_type"] for r in train_rows])
    labels = list(model.classes_)

    def cls_metrics(subset: list[dict[str, Any]]) -> dict[str, Any]:
        if not subset:
            return {}
        proba = model.predict_proba([r["text"] for r in subset])
        pred = [labels[i] for i in proba.argmax(1)]
        conf = proba.max(1)
        y = [r["doc_type"] for r in subset]
        accepted = conf >= REJECT_THRESHOLD
        return {"n": len(subset), "accuracy": round(float(accuracy_score(y, pred)), 4),
                "macro_f1": round(float(f1_score(y, pred, average="macro")), 4),
                "coverage_at_threshold": round(float(accepted.mean()), 4),
                "accuracy_when_accepted": round(float(np.mean([p == t for p, t, a in zip(pred, y, accepted) if a])), 4) if accepted.any() else None,
                "mean_confidence": round(float(conf.mean()), 4)}

    test_native = [r for r in test_rows if not r["scanned"]]
    test_scan = [r for r in test_rows if r["scanned"]]
    proba_all = model.predict_proba([r["text"] for r in test_rows])
    cm = confusion_matrix([r["doc_type"] for r in test_rows], [labels[i] for i in proba_all.argmax(1)], labels=labels)
    ood = _ood_texts()
    ood_conf = model.predict_proba(ood).max(1) if ood else np.array([])
    ocr_conf = [r["ocr_conf"] for r in rows if r["scanned"] and r["ocr_conf"] is not None]
    metrics: dict[str, Any] = {
        "protocol": {"train_templates": list(TRAIN_TEMPLATES), "test_templates": list(TEST_TEMPLATES), "reject_threshold": REJECT_THRESHOLD,
                     "docs_train": len(train_rows), "docs_test": len(test_rows), "scans_per_type_template": n_scan,
                     "reference_date": synth.REFERENCE_DATE.isoformat()},
        "classification": {"test_unseen_layout": cls_metrics(test_rows), "test_native": cls_metrics(test_native),
                           "test_scanned": cls_metrics(test_scan), "train": cls_metrics(train_rows),
                           "confusion_matrix": {"labels": labels, "matrix": cm.tolist()}},
        "ood_rejection": {"n": len(ood), "rejected_rate": round(float((ood_conf < REJECT_THRESHOLD).mean()), 4) if len(ood) else None,
                          "source": "groups of 15 BANKING77 customer messages (not documents)"},
        "ocr": {"mean_tesseract_confidence": round(float(np.mean(ocr_conf)), 2) if ocr_conf else None, "engine": "Tesseract 5 (fra+eng)"},
        "extraction_all_layouts": _evaluate_extraction(rows),
        "extraction_unseen_layout": _evaluate_extraction(test_rows),
    }
    card = {"model": MODEL_NAME,
            "intended_use": "Pre-sort and pre-fill KYC / KYB supporting documents for human review. Never an identity verification or a certification.",
            "training_data": "Synthetic DEMO specimens (5 types × 3 layouts) generated by BANKVERSE, native PDFs and degraded scans. No real document.",
            "limitations": ["Synthetic layouts are regular: real-world accuracy will be lower and must be measured on a labelled pilot corpus.",
                            "Rule-based field extraction is layout-anchored on French labels; new issuers need new anchors or a learned extractor (e.g. Textract / LayoutLM).",
                            "Identity documents (ID cards, passports) are intentionally out of scope in the demo environment.",
                            "Consistency controls flag anomalies; they are not fraud findings."],
            "human_oversight": "Every document stays in 'needs_review' until an analyst validates or corrects the extracted fields."}
    with control_session() as db:
        version = registry.next_version(db, MODEL_NAME)
    adir = registry.artifact_dir(MODEL_NAME, version)
    artifact = adir / "model.joblib"
    joblib.dump({"classifier": model, "reject_threshold": REJECT_THRESHOLD, "labels": labels}, artifact, compress=3)
    flat = {"cls_unseen_acc": metrics["classification"]["test_unseen_layout"]["accuracy"],
            "cls_scan_acc": metrics["classification"]["test_scanned"].get("accuracy", 0),
            "field_acc_native": metrics["extraction_all_layouts"]["field_accuracy"]["native"] or 0,
            "field_acc_scan": metrics["extraction_all_layouts"]["field_accuracy"]["scan"] or 0}
    with tracking.run("document_classifier", f"v{version}", tags={"module": "documents"}) as run_id:
        tracking.log_metrics(flat)
    with control_session(owner=True) as db:
        registry.register_version(db, name=MODEL_NAME, module="documents", task="document type classification + rule-based extraction",
                                  description="KYC/KYB document classifier (TF-IDF word+char, logistic regression) evaluated on an unseen layout.",
                                  version=version, algorithm="tfidf (word+char) + logistic regression", artifact_path=artifact, metrics=metrics,
                                  params={"C": 5, "reject_threshold": REJECT_THRESHOLD}, features=["page_text"], dataset_versions=[],
                                  reference_profile={}, model_card=card, mlflow_run_id=run_id, trained_by=trained_by,
                                  training_seconds=time.perf_counter() - t0, risk_tier="limited")
    log.info("documents_training_done", version=version, **flat)
    return {"model": MODEL_NAME, "version": version, **flat, "ood_rejected": metrics["ood_rejection"]["rejected_rate"]}
